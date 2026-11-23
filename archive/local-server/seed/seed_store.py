"""PostgreSQL event journal. One committed INSERT advances one task."""
import json
import os
import subprocess
from uuid import UUID, uuid4


def sql(query):
    proc = subprocess.run(["psql", "-X", "-qAtw", "-v", "ON_ERROR_STOP=1",
                           "-d", os.environ.get("PGDATABASE", "project_guide_seed")],
                          input=query, capture_output=True, text=True, timeout=12,
                          env={**os.environ, "PGCONNECT_TIMEOUT": "5", "PGCLIENTENCODING": "UTF8"})
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout.strip()


def init():
    sql("""CREATE TABLE IF NOT EXISTS seed_events (
        task uuid NOT NULL, seq integer NOT NULL, state text NOT NULL,
        data jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(task, seq)
    );""")

    stage_init()
    project_init()


def append(task, previous, state, data):
    # JSON is passed as UTF-8 hex data, never as SQL or psql commands.
    payload = json.dumps(data, ensure_ascii=False).encode().hex()
    if not state.isidentifier():
        raise ValueError("Invalid state")
    result = sql(f"""INSERT INTO seed_events(task,seq,state,data)
        SELECT '{UUID(task)}', {previous + 1}, '{state}',
               convert_from(decode('{payload}','hex'),'UTF8')::jsonb
        WHERE COALESCE((SELECT max(seq) FROM seed_events WHERE task='{UUID(task)}'),0)={previous}
        ON CONFLICT DO NOTHING RETURNING seq;""")
    if not result:
        raise ValueError("任务已更新，请刷新页面。")


def create(goal, acceptance):
    task = str(uuid4())
    append(task, 0, "queued", {"goal": goal, "acceptance": acceptance})
    return task


def journal():
    rows = sql("SELECT row_to_json(e) FROM seed_events e ORDER BY recorded_at, task, seq;")
    tasks = {}
    for row in rows.splitlines():
        event = json.loads(row)
        tasks.setdefault(event["task"], []).append(event)
    for events in tasks.values():
        events.sort(key=lambda e: e["seq"])
    return tasks


# Stage snapshots are immutable; the singleton row is the PostgreSQL coordination lock.
def payload(value):
    return "convert_from(decode('%s','hex'),'UTF8')::jsonb" % json.dumps(value, ensure_ascii=False).encode().hex()


def stage_init():
    sql('''CREATE TABLE IF NOT EXISTS seed_stage (
        singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
        id uuid NOT NULL UNIQUE, version integer NOT NULL, state text NOT NULL, data jsonb NOT NULL);
        CREATE TABLE IF NOT EXISTS seed_stage_versions (
        stage uuid NOT NULL REFERENCES seed_stage(id), version integer NOT NULL,
        data jsonb NOT NULL, PRIMARY KEY(stage,version));
        CREATE TABLE IF NOT EXISTS seed_stage_events (
        id bigserial PRIMARY KEY, stage uuid NOT NULL REFERENCES seed_stage(id),
        kind text NOT NULL, data jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE IF NOT EXISTS seed_stage_authorizations (
        stage uuid PRIMARY KEY REFERENCES seed_stage(id), version integer NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE IF NOT EXISTS seed_stage_tasks (
        stage uuid NOT NULL REFERENCES seed_stage(id), task uuid NOT NULL UNIQUE,
        PRIMARY KEY(stage,task));''')


def stage_get():
    result = sql('''SELECT row_to_json(s) FROM (
        SELECT id,version,state,data,
        (SELECT version FROM seed_stage_authorizations a WHERE a.stage=seed_stage.id) authorized_version,
        COALESCE((SELECT jsonb_agg(task ORDER BY task) FROM seed_stage_tasks t WHERE t.stage=seed_stage.id),'[]'::jsonb) tasks
        FROM seed_stage) s;''')
    if not result:
        return None
    row = json.loads(result)
    return {**row.pop('data'), **row}


def stage_create(goal, acceptance):
    ident = str(uuid4())
    data = payload(dict(goal=goal, acceptance=acceptance, cards=[]))
    sql(f'''BEGIN;
        INSERT INTO seed_stage(id,version,state,data) VALUES ('{ident}',0,'pending',{data});
        INSERT INTO seed_stage_events(stage,kind,data) VALUES ('{ident}','requested',{data}); COMMIT;''')
    return ident


def stage_update(ident, expected, state, data, allowed):
    ident = str(UUID(ident))
    if state not in ('pending', 'planning', 'failed', 'draft') or not set(allowed) <= {'pending', 'planning', 'failed', 'draft'}:
        raise ValueError('无效阶段状态')
    allowed_sql = ','.join("'%s'" % x for x in allowed)
    value = payload(data)
    sql(f'''BEGIN; DO $stage$
        DECLARE s seed_stage%ROWTYPE;
        BEGIN SELECT * INTO s FROM seed_stage WHERE id='{ident}' FOR UPDATE;
        IF s.id IS NULL OR s.version != {int(expected)} OR s.state NOT IN ({allowed_sql}) THEN
            RAISE EXCEPTION '阶段版本已更新，请核对最新版本；输入已保留'; END IF;
        UPDATE seed_stage SET version=version+1,state='{state}',data=data || {value} WHERE id=s.id;
        INSERT INTO seed_stage_versions SELECT id,version,data FROM seed_stage WHERE id=s.id;
        INSERT INTO seed_stage_events(stage,kind,data) VALUES(s.id,'{state}',{value});
        END $stage$; COMMIT;''')


def stage_save(ident, expected, cards):
    import seed_stage
    seed_stage.validate(cards)
    current = stage_get()
    if not current or set(c['id'] for c in cards) != set(c['id'] for c in current['cards']):
        raise ValueError('编辑不能更换任务标识')
    stage_update(ident, expected, 'draft', {'cards': cards}, ['draft'])


def stage_authorize(ident, expected):
    import seed_stage
    ident, expected = str(UUID(ident)), int(expected)
    # Validate the immutable version, then compare it again under the database row lock.
    raw = sql(f"SELECT data FROM seed_stage_versions WHERE stage='{ident}' AND version={expected};")
    if not raw:
        raise ValueError('阶段版本不存在')
    snapshot = json.loads(raw)
    seed_stage.validate(snapshot['cards'])
    sql(f'''BEGIN; DO $stage$
        DECLARE s seed_stage%ROWTYPE; c jsonb;
        BEGIN SELECT * INTO s FROM seed_stage WHERE id='{ident}' FOR UPDATE;
        IF s.state IN ('authorized','accepted') AND EXISTS(
            SELECT 1 FROM seed_stage_authorizations WHERE stage=s.id AND version={expected}) THEN RETURN; END IF;
        IF s.id IS NULL OR s.version != {expected} THEN RAISE EXCEPTION '阶段版本已更新，请刷新'; END IF;
        IF s.state != 'draft' OR s.data != {payload(snapshot)} THEN RAISE EXCEPTION '草稿已变化或不可授权'; END IF;
        INSERT INTO seed_stage_authorizations(stage,version) VALUES(s.id,s.version);
        FOR c IN SELECT * FROM jsonb_array_elements(s.data->'cards') LOOP
            INSERT INTO seed_events(task,seq,state,data) VALUES ((c->>'id')::uuid,1,'queued',
                c || jsonb_build_object('stage_id',s.id,'draft_version',s.version));
            INSERT INTO seed_stage_tasks(stage,task) VALUES(s.id,(c->>'id')::uuid);
        END LOOP;
        UPDATE seed_stage SET state='authorized' WHERE id=s.id;
        INSERT INTO seed_stage_events(stage,kind,data) VALUES(s.id,'authorized',jsonb_build_object('version',s.version));
        END $stage$; COMMIT;''')
    return stage_get()['tasks']


def stage_accept(ident, expected, note, passed=True):
    ident = str(UUID(ident))
    if not note.strip() or len(note) > 12000:
        raise ValueError('请填写阶段验收说明')
    next_state = "accepted" if passed else "authorized"
    assessment = {'assessment': note, 'passed': passed, 'assessment_request_version': int(expected)}
    sql(f'''BEGIN; DO $stage$
        DECLARE s seed_stage%ROWTYPE; commits jsonb;
        BEGIN SELECT * INTO s FROM seed_stage WHERE id='{ident}' FOR UPDATE;
        IF s.data @> {payload(assessment)} THEN RETURN; END IF;
        IF s.id IS NULL OR s.version != {int(expected)} THEN RAISE EXCEPTION '阶段版本已更新'; END IF;
        IF s.state='accepted' THEN RAISE EXCEPTION '阶段已经验收通过'; END IF;
        IF s.state != 'authorized' OR NOT EXISTS(SELECT 1 FROM seed_stage_tasks WHERE stage=s.id)
        OR EXISTS(SELECT 1 FROM seed_stage_tasks t WHERE t.stage=s.id AND
            (SELECT state FROM seed_events e WHERE e.task=t.task ORDER BY seq DESC LIMIT 1) IS DISTINCT FROM 'activated')
        THEN RAISE EXCEPTION '全部子任务人工启用后才能阶段验收'; END IF;
        SELECT jsonb_object_agg(t.task,(SELECT data->>'candidate' FROM seed_events e
            WHERE e.task=t.task AND data ? 'candidate' ORDER BY seq DESC LIMIT 1)) INTO commits
            FROM seed_stage_tasks t WHERE t.stage=s.id;
        INSERT INTO seed_stage_events(stage,kind,data) VALUES(s.id,'assessment',
            {payload({'note': note, 'passed': passed})} || jsonb_build_object('commits',commits));
        UPDATE seed_stage SET state='{next_state}',version=version+1,
            data=data || {payload(assessment)} WHERE id=s.id;
        END $stage$; COMMIT;''')


def stage_recover():
    current = stage_get()
    if current and current['state'] == 'planning':
        stage_update(current['id'], current['version'], 'failed',
                     {'error': '服务中断，请确认旧主 Agent 已退出后重试规划'}, ['planning'])


# Project materials: short transactions only; Git operations use the common-dir lock.
def project_init():
    sql('''CREATE TABLE IF NOT EXISTS seed_project (
        singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), data jsonb NOT NULL);
        CREATE TABLE IF NOT EXISTS seed_project_documents (
        id text PRIMARY KEY, data jsonb NOT NULL);
        CREATE TABLE IF NOT EXISTS seed_project_drafts (
        version integer PRIMARY KEY, data jsonb NOT NULL);
        CREATE TABLE IF NOT EXISTS seed_project_publications (
        request_id uuid PRIMARY KEY, data jsonb NOT NULL);
        CREATE TABLE IF NOT EXISTS seed_project_events (
        request_id uuid NOT NULL, kind text NOT NULL, data jsonb NOT NULL,
        PRIMARY KEY(request_id,kind));''')


def project_get():
    raw = sql('SELECT data FROM seed_project WHERE singleton;')
    return json.loads(raw) if raw else None


def project_start(data):
    for doc in data['documents']:
        sql(f"INSERT INTO seed_project_documents VALUES ({payload(doc)}->>'id',{payload(doc)}) ON CONFLICT DO NOTHING;")
    sql(f'INSERT INTO seed_project(data) VALUES ({payload(data)}) ON CONFLICT DO NOTHING;')
    return project_get()


def project_drafts():
    return [json.loads(row) for row in sql(
        'SELECT data FROM seed_project_drafts ORDER BY version DESC;').splitlines()]


def project_draft():
    raw = sql('SELECT data FROM seed_project_drafts ORDER BY version DESC LIMIT 1;')
    return json.loads(raw) if raw else None


def project_save(expected, data):
    sql(f'''BEGIN; DO $project$ BEGIN
        PERFORM 1 FROM seed_project WHERE singleton FOR UPDATE;
        IF COALESCE((SELECT max(version) FROM seed_project_drafts),0) != {int(expected)}
        OR (SELECT data->>'published' FROM seed_project) IS DISTINCT FROM {payload(data)}->>'source_commit'
        OR EXISTS(SELECT 1 FROM seed_project_publications WHERE data->>'state'='activating')
        THEN RAISE EXCEPTION 'project version conflict'; END IF;
        INSERT INTO seed_project_drafts VALUES ({int(expected)+1},{payload(data)});
        END $project$; COMMIT;''')
    return data


def project_publications():
    return [json.loads(row) for row in sql(
        'SELECT data FROM seed_project_publications ORDER BY request_id;').splitlines()]


def project_publication(ident):
    raw = sql(f"SELECT data FROM seed_project_publications WHERE request_id='{UUID(ident)}';")
    return json.loads(raw) if raw else None


def project_request(data):
    ident = str(UUID(data['request_id']))
    sql(f'''BEGIN; DO $project$ DECLARE old jsonb; BEGIN
        PERFORM 1 FROM seed_project WHERE singleton FOR UPDATE;
        SELECT data INTO old FROM seed_project_publications WHERE request_id='{ident}';
        IF old IS NOT NULL THEN
            IF old->>'fingerprint' != {payload(data)}->>'fingerprint' THEN
                RAISE EXCEPTION 'project request conflict'; END IF;
            RETURN;
        END IF;
        IF (SELECT max(version) FROM seed_project_drafts) IS DISTINCT FROM {int(data['draft_version'])}
        THEN RAISE EXCEPTION 'project stale draft'; END IF;
        INSERT INTO seed_project_publications VALUES('{ident}',{payload(data)});
        END $project$; COMMIT;''')
    return project_publication(ident)


def project_transition(data, activated=False):
    ident = str(UUID(data['request_id']))
    extra = ''
    if activated:
        extra = f'''UPDATE seed_project SET data=jsonb_set(data,'{{published}}',
            {payload(data['candidate_commit'])}) WHERE singleton;
            INSERT INTO seed_project_events VALUES('{ident}','activated',{payload(data)})
            ON CONFLICT DO NOTHING;'''
    sql(f'''BEGIN; SELECT singleton FROM seed_project WHERE singleton FOR UPDATE;
        UPDATE seed_project_publications SET data={payload(data)} WHERE request_id='{ident}';
        {extra} COMMIT;''')
