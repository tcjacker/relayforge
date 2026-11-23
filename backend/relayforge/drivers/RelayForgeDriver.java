// 测试集的 Java 驱动：协议和 python_driver.py 一样（见 FORMAT.md 的「测试集」）。
//   标准输入一份 JSON {"protocol":1,"root","entry","class":"接入代码的类名","cases":[{"n","input"}…]}；
//   标准输出一行一个 JSON：ready / load_error / case / done。
// 接入代码：一个类，有 public static Object run(Object input)（参数也可以是 Map、List、String、数字、boolean、数组、record、
// 普通 Java Bean 这些能从 JSON 值对上的类型）。返回值：null、Map、List/Collection/数组、String、Number、Boolean、枚举（名字）、
// Optional、record、有 getter 的 Bean；别的类型报错，不猜。
// 不依赖任何第三方库（自带很小的 JSON 读写），用反射调用。写 Java 8 能编译的代码，驱动运行在用户项目的 JDK 上。
// 被测代码写到 System.out 的东西转到标准错误。程序（runners.py）把它编译到缓存目录再运行，不要求用户预先编译。
import java.io.ByteArrayOutputStream;
import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.PrintStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.lang.reflect.Array;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;

public class RelayForgeDriver {
    static PrintStream protocol;

    static void say(String json) {
        protocol.println(json);
        protocol.flush();
    }

    public static void main(String[] args) throws Exception {
        protocol = new PrintStream(new FileOutputStream(FileDescriptor.out), true, "UTF-8");
        System.setOut(System.err); // 被测代码打印的东西去标准错误
        Object req;
        try {
            req = new Json(new String(readAll(System.in), StandardCharsets.UTF_8)).parseAll();
        } catch (RuntimeException e) {
            say(obj("t", "load_error", "error", "驱动收到的不是 JSON：" + e.getMessage(), "trace", ""));
            return;
        }
        Map<?, ?> r = (Map<?, ?>) req;
        String className = String.valueOf(r.get("class"));
        List<Method> candidates = new ArrayList<Method>();
        try {
            Class<?> k = Class.forName(className, true, RelayForgeDriver.class.getClassLoader());
            for (Method m : k.getMethods()) {
                if (m.getName().equals("run") && Modifier.isStatic(m.getModifiers()) && m.getParameterCount() == 1) candidates.add(m);
            }
            if (candidates.isEmpty()) {
                throw new NoSuchMethodException("类 " + className + " 没有 public static Object run(Object input)（要 public、static、只有一个参数）");
            }
        } catch (ClassNotFoundException e) {
            say(obj("t", "load_error", "error", "ClassNotFoundException: 找不到类 " + className + "（检查类路径；接入代码是 .java 时看有没有编译成功）", "trace", trace(e)));
            return;
        } catch (Throwable e) {
            Throwable t = e instanceof ExceptionInInitializerError && e.getCause() != null ? e.getCause() : e;
            say(obj("t", "load_error", "error", name(t) + ": " + t.getMessage(), "trace", trace(t)));
            return;
        }
        say(obj("t", "ready", "protocol", 1));
        Object cases = r.get("cases");
        if (cases instanceof List) {
            for (Object o : (List<?>) cases) runCase((Map<?, ?>) o, candidates);
        }
        say(obj("t", "done"));
    }

    static void runCase(Map<?, ?> c, List<Method> candidates) {
        long started = System.nanoTime();
        Object out;
        try {
            out = invoke(candidates, c.get("input"));
        } catch (InvocationTargetException e) {
            Throwable t = e.getCause() == null ? e : e.getCause();
            say(obj("t", "case", "n", c.get("n"), "ok", false, "error", name(t) + ": " + t.getMessage(), "trace", trace(t), "ms", ms(started)));
            return;
        } catch (Throwable t) {
            say(obj("t", "case", "n", c.get("n"), "ok", false, "error", name(t) + ": " + t.getMessage(), "trace", trace(t), "ms", ms(started)));
            return;
        }
        double took = ms(started);
        try {
            StringBuilder sb = new StringBuilder();
            write(sb, out, 0);
            say("{\"t\":\"case\",\"n\":" + Json.render(c.get("n")) + ",\"ok\":true,\"output\":" + sb + ",\"ms\":" + took + "}");
        } catch (RuntimeException e) {
            say(obj("t", "case", "n", c.get("n"), "ok", false, "error", "返回值不能转成 JSON：" + e.getMessage(), "trace", "", "ms", took));
        }
    }

    static Object invoke(List<Method> candidates, Object input) throws Exception {
        Method chosen = null;
        Object arg = null;
        RuntimeException first = null;
        for (Method m : candidates) {
            try {
                arg = convert(input, m.getParameterTypes()[0], m.getGenericParameterTypes()[0]);
                chosen = m;
                break;
            } catch (RuntimeException e) {
                if (first == null) first = e;
            }
        }
        if (chosen == null) throw new IllegalArgumentException("输入对不上 run 的参数类型：" + (first == null ? "" : first.getMessage()));
        chosen.setAccessible(true);
        return chosen.invoke(null, arg);
    }

    // ---------- 输入：JSON 值 → 参数类型 ----------

    static Object convert(Object v, Class<?> type, java.lang.reflect.Type generic) throws Exception {
        if (v == null) {
            if (type.isPrimitive()) throw new IllegalArgumentException("null 放不进 " + type.getName());
            return null;
        }
        if (type == Object.class) return v;
        if (type.isInstance(v) && !(v instanceof Map) && !(v instanceof List)) return v;
        if (type == String.class || type == CharSequence.class) {
            if (v instanceof String) return v;
            throw bad(v, type);
        }
        if (v instanceof Boolean && (type == boolean.class || type == Boolean.class)) return v;
        if (v instanceof Number && (Number.class.isAssignableFrom(type) || type.isPrimitive() && type != boolean.class && type != char.class)) return number((Number) v, type);
        if (type.isEnum() && v instanceof String) {
            for (Object k : type.getEnumConstants()) if (((Enum<?>) k).name().equals(v)) return k;
            throw bad(v, type);
        }
        if (v instanceof List) {
            List<?> list = (List<?>) v;
            if (type.isArray()) {
                Object arr = Array.newInstance(type.getComponentType(), list.size());
                for (int i = 0; i < list.size(); i++) Array.set(arr, i, convert(list.get(i), type.getComponentType(), type.getComponentType()));
                return arr;
            }
            if (type.isAssignableFrom(ArrayList.class)) { // List、Collection、Iterable
                Class<?> item = itemType(generic, 0);
                List<Object> out = new ArrayList<Object>();
                for (Object o : list) out.add(item == null ? o : convert(o, item, item));
                return out;
            }
            throw bad(v, type);
        }
        if (v instanceof Map) {
            Map<?, ?> map = (Map<?, ?>) v;
            if (type.isAssignableFrom(LinkedHashMap.class)) { // Map、LinkedHashMap 等
                Class<?> item = itemType(generic, 1);
                if (item == null || item == Object.class) return map;
                Map<Object, Object> out = new LinkedHashMap<Object, Object>();
                for (Map.Entry<?, ?> e : map.entrySet()) out.put(e.getKey(), convert(e.getValue(), item, item));
                return out;
            }
            return bean(map, type);
        }
        throw bad(v, type);
    }

    static Class<?> itemType(java.lang.reflect.Type generic, int at) {
        if (generic instanceof java.lang.reflect.ParameterizedType) {
            java.lang.reflect.Type[] a = ((java.lang.reflect.ParameterizedType) generic).getActualTypeArguments();
            if (a.length > at && a[at] instanceof Class) return (Class<?>) a[at];
        }
        return null;
    }

    static Object number(Number n, Class<?> t) {
        if (t == int.class || t == Integer.class) return exact(n, Integer.MIN_VALUE, Integer.MAX_VALUE).intValue();
        if (t == long.class || t == Long.class) return exact(n, Long.MIN_VALUE, Long.MAX_VALUE).longValue();
        if (t == short.class || t == Short.class) return exact(n, Short.MIN_VALUE, Short.MAX_VALUE).shortValue();
        if (t == byte.class || t == Byte.class) return exact(n, Byte.MIN_VALUE, Byte.MAX_VALUE).byteValue();
        if (t == double.class || t == Double.class) return n.doubleValue();
        if (t == float.class || t == Float.class) return n.floatValue();
        if (t == BigDecimal.class) return new BigDecimal(n.toString());
        if (t == BigInteger.class) return new BigDecimal(n.toString()).toBigIntegerExact();
        if (t == Number.class) return n;
        throw bad(n, t);
    }

    static Number exact(Number n, long min, long max) {
        double d = n.doubleValue();
        if (d != Math.rint(d) || d < min || d > max) throw new IllegalArgumentException(n + " 放不进整数类型（有小数或超出范围）");
        return n;
    }

    // record（规范构造器）或普通 Bean（无参构造器 + setter 或 public 字段）
    static Object bean(Map<?, ?> map, Class<?> type) throws Exception {
        if (type.isInterface() || Modifier.isAbstract(type.getModifiers())) throw bad(map, type);
        if (isRecord(type)) {
            Object[] comps = (Object[]) Class.class.getMethod("getRecordComponents").invoke(type);
            Class<?>[] types = new Class<?>[comps.length];
            Object[] args = new Object[comps.length];
            for (int i = 0; i < comps.length; i++) {
                Method getType = comps[i].getClass().getMethod("getType");
                Method getName = comps[i].getClass().getMethod("getName");
                getType.setAccessible(true);
                getName.setAccessible(true);
                types[i] = (Class<?>) getType.invoke(comps[i]);
                Object value = map.get(getName.invoke(comps[i]));
                args[i] = value == null && types[i].isPrimitive() ? Array.get(Array.newInstance(types[i], 1), 0) : convert(value, types[i], types[i]);
            }
            Constructor<?> k = type.getDeclaredConstructor(types);
            k.setAccessible(true);
            return k.newInstance(args);
        }
        Constructor<?> k = type.getDeclaredConstructor();
        k.setAccessible(true);
        Object o = k.newInstance();
        for (Map.Entry<?, ?> e : map.entrySet()) {
            String key = String.valueOf(e.getKey());
            String setter = "set" + Character.toUpperCase(key.charAt(0)) + key.substring(1);
            Method found = null;
            for (Method m : type.getMethods()) if (m.getName().equals(setter) && m.getParameterCount() == 1) found = m;
            if (found != null) {
                found.invoke(o, convert(e.getValue(), found.getParameterTypes()[0], found.getGenericParameterTypes()[0]));
                continue;
            }
            try {
                Field f = type.getField(key);
                f.set(o, convert(e.getValue(), f.getType(), f.getGenericType()));
            } catch (NoSuchFieldException x) {
                throw new IllegalArgumentException(type.getSimpleName() + " 没有 " + key + " 这个属性（要有 setter 或 public 字段）");
            }
        }
        return o;
    }

    static boolean isRecord(Class<?> type) {
        try {
            return (Boolean) Class.class.getMethod("isRecord").invoke(type);
        } catch (Exception e) {
            return false; // Java 16 以前没有 record
        }
    }

    static IllegalArgumentException bad(Object v, Class<?> type) {
        return new IllegalArgumentException("JSON 值 " + Json.render(v) + " 对不上参数类型 " + type.getName());
    }

    // ---------- 输出：返回值 → JSON ----------

    static void write(StringBuilder sb, Object v, int depth) {
        if (depth > 200) throw new IllegalArgumentException("嵌套太深（有循环引用？）");
        if (v == null) { sb.append("null"); return; }
        if (v instanceof String || v instanceof Character) { Json.quote(sb, v.toString()); return; }
        if (v instanceof Boolean) { sb.append(v); return; }
        if (v instanceof Double || v instanceof Float) {
            double d = ((Number) v).doubleValue();
            if (Double.isNaN(d) || Double.isInfinite(d)) throw new IllegalArgumentException("返回值里有 " + d + "，JSON 表示不了");
            sb.append(v instanceof Float ? v.toString() : Double.toString(d));
            return;
        }
        if (v instanceof BigDecimal) { sb.append(((BigDecimal) v).toString()); return; }
        if (v instanceof Number) { // Integer、Long、Short、Byte、BigInteger、AtomicInteger…
            if (v instanceof BigInteger || v instanceof Integer || v instanceof Long || v instanceof Short || v instanceof Byte) sb.append(v);
            else sb.append(((Number) v).doubleValue());
            return;
        }
        if (v instanceof Enum) { Json.quote(sb, ((Enum<?>) v).name()); return; }
        if (v instanceof Optional) { write(sb, ((Optional<?>) v).orElse(null), depth + 1); return; }
        if (v instanceof Map) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                if (!first) sb.append(',');
                first = false;
                Json.quote(sb, String.valueOf(e.getKey()));
                sb.append(':');
                write(sb, e.getValue(), depth + 1);
            }
            sb.append('}');
            return;
        }
        if (v instanceof Collection || v.getClass().isArray()) {
            sb.append('[');
            boolean first = true;
            if (v instanceof Collection) {
                for (Object o : (Collection<?>) v) {
                    if (!first) sb.append(',');
                    first = false;
                    write(sb, o, depth + 1);
                }
            } else {
                for (int i = 0, n = Array.getLength(v); i < n; i++) {
                    if (i > 0) sb.append(',');
                    write(sb, Array.get(v, i), depth + 1);
                }
            }
            sb.append(']');
            return;
        }
        write(sb, beanMap(v), depth + 1);
    }

    // record 的组件，或 Bean 的 getter / public 字段，按名字排序（顺序不影响比对）
    static Map<String, Object> beanMap(Object v) {
        Class<?> type = v.getClass();
        Map<String, Object> out = new TreeMap<String, Object>();
        try {
            if (isRecord(type)) {
                for (Object comp : (Object[]) Class.class.getMethod("getRecordComponents").invoke(type)) {
                    Method getName = comp.getClass().getMethod("getName");
                    Method getAccessor = comp.getClass().getMethod("getAccessor");
                    getName.setAccessible(true);
                    getAccessor.setAccessible(true);
                    Method accessor = (Method) getAccessor.invoke(comp);
                    accessor.setAccessible(true);
                    out.put((String) getName.invoke(comp), accessor.invoke(v));
                }
                return out;
            }
            for (Method m : type.getMethods()) {
                String n = m.getName();
                if (m.getParameterCount() != 0 || Modifier.isStatic(m.getModifiers()) || m.getDeclaringClass() == Object.class || m.getReturnType() == void.class) continue;
                String prop = null;
                if (n.startsWith("get") && n.length() > 3) prop = n.substring(3);
                else if (n.startsWith("is") && n.length() > 2 && (m.getReturnType() == boolean.class || m.getReturnType() == Boolean.class)) prop = n.substring(2);
                if (prop != null) out.put(Character.toLowerCase(prop.charAt(0)) + prop.substring(1), m.invoke(v));
            }
            for (Field f : type.getFields()) if (!Modifier.isStatic(f.getModifiers())) out.put(f.getName(), f.get(v));
        } catch (InvocationTargetException e) {
            throw new IllegalArgumentException("读 " + type.getName() + " 的属性时抛了 " + e.getCause());
        } catch (Exception e) {
            throw new IllegalArgumentException(e.toString());
        }
        if (out.isEmpty()) {
            throw new IllegalArgumentException("不支持的返回类型 " + type.getName() + "（转成 Map、List、String、数字、boolean，或 record、有 getter 的 Bean）");
        }
        return out;
    }

    // ---------- 小工具 ----------

    static double ms(long started) {
        return Math.round((System.nanoTime() - started) / 1e3) / 1e3;
    }

    static String name(Throwable t) {
        return t.getClass().getName();
    }

    static String trace(Throwable t) {
        StringWriter w = new StringWriter();
        t.printStackTrace(new PrintWriter(w));
        String s = w.toString();
        return s.length() > 4000 ? s.substring(s.length() - 4000) : s;
    }

    static byte[] readAll(InputStream in) throws java.io.IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
        return out.toByteArray();
    }

    static String obj(Object... kv) {
        StringBuilder sb = new StringBuilder("{");
        for (int i = 0; i < kv.length; i += 2) {
            if (i > 0) sb.append(',');
            Json.quote(sb, (String) kv[i]);
            sb.append(':').append(Json.render(kv[i + 1]));
        }
        return sb.append('}').toString();
    }

    // ---------- 很小的 JSON 读写 ----------

    static final class Json {
        final String s;
        int i;

        Json(String s) { this.s = s; }

        Object parseAll() {
            ws();
            Object v = value();
            ws();
            if (i < s.length()) throw new IllegalArgumentException("第 " + i + " 个字符之后还有多余的内容");
            return v;
        }

        void ws() { while (i < s.length() && " \t\r\n".indexOf(s.charAt(i)) >= 0) i++; }

        Object value() {
            if (i >= s.length()) throw new IllegalArgumentException("JSON 提前结束了");
            char c = s.charAt(i);
            if (c == '{') {
                i++;
                Map<String, Object> m = new LinkedHashMap<String, Object>();
                ws();
                if (peek('}')) return m;
                do {
                    ws();
                    String k = string();
                    ws();
                    expect(':');
                    ws();
                    m.put(k, value());
                    ws();
                } while (comma('}'));
                return m;
            }
            if (c == '[') {
                i++;
                List<Object> l = new ArrayList<Object>();
                ws();
                if (peek(']')) return l;
                do {
                    ws();
                    l.add(value());
                    ws();
                } while (comma(']'));
                return l;
            }
            if (c == '"') return string();
            if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
            if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            if (s.startsWith("null", i)) { i += 4; return null; }
            return number();
        }

        boolean peek(char c) {
            if (i < s.length() && s.charAt(i) == c) { i++; return true; }
            return false;
        }

        boolean comma(char close) {
            if (peek(',')) return true;
            expect(close);
            return false;
        }

        void expect(char c) {
            if (!peek(c)) throw new IllegalArgumentException("第 " + i + " 个字符处应该是 " + c);
        }

        Object number() {
            int start = i;
            while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) i++;
            String t = s.substring(start, i);
            if (t.isEmpty()) throw new IllegalArgumentException("第 " + start + " 个字符处不是 JSON 值");
            try {
                if (t.indexOf('.') < 0 && t.indexOf('e') < 0 && t.indexOf('E') < 0) {
                    BigInteger b = new BigInteger(t);
                    return b.bitLength() < 64 ? (Object) b.longValue() : (Object) b.doubleValue();
                }
                return Double.parseDouble(t);
            } catch (NumberFormatException e) {
                throw new IllegalArgumentException("第 " + start + " 个字符处的数字 " + t + " 不对");
            }
        }

        String string() {
            expect('"');
            StringBuilder sb = new StringBuilder();
            while (true) {
                if (i >= s.length()) throw new IllegalArgumentException("字符串没有结束");
                char c = s.charAt(i++);
                if (c == '"') return sb.toString();
                if (c != '\\') { sb.append(c); continue; }
                char e = s.charAt(i++);
                switch (e) {
                    case 'n': sb.append('\n'); break;
                    case 't': sb.append('\t'); break;
                    case 'r': sb.append('\r'); break;
                    case 'b': sb.append('\b'); break;
                    case 'f': sb.append('\f'); break;
                    case 'u': sb.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); i += 4; break;
                    default: sb.append(e); // " \ /
                }
            }
        }

        static void quote(StringBuilder sb, String t) {
            sb.append('"');
            for (int k = 0; k < t.length(); k++) {
                char c = t.charAt(k);
                switch (c) {
                    case '"': sb.append("\\\""); break;
                    case '\\': sb.append("\\\\"); break;
                    case '\n': sb.append("\\n"); break;
                    case '\r': sb.append("\\r"); break;
                    case '\t': sb.append("\\t"); break;
                    default:
                        if (c < 0x20 || c == 0x2028 || c == 0x2029) sb.append(String.format("\\u%04x", (int) c));
                        else sb.append(c);
                }
            }
            sb.append('"');
        }

        // 驱动自己的协议行用：null、字符串、布尔、数字
        static String render(Object v) {
            if (v == null) return "null";
            if (v instanceof String) { StringBuilder sb = new StringBuilder(); quote(sb, (String) v); return sb.toString(); }
            if (v instanceof Map || v instanceof List) { StringBuilder sb = new StringBuilder(); write(sb, v, 0); return sb.toString(); }
            return v.toString();
        }
    }
}
