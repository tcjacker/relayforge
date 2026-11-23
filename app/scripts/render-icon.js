// Run with macOS's built-in JavaScript for Automation; no compiler or packages required.
ObjC.import('AppKit');

function run(args) {
  const source = $.NSImage.alloc.initWithContentsOfFile(args[0]);
  if (!source) throw new Error('Cannot read source icon');
  const bitmap = $.NSBitmapImageRep.alloc
    .initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(
      null, 1024, 1024, 8, 4, true, false, $.NSDeviceRGBColorSpace, 0, 0);
  const context = $.NSGraphicsContext.graphicsContextWithBitmapImageRep(bitmap);
  $.NSGraphicsContext.saveGraphicsState;
  $.NSGraphicsContext.setCurrentContext(context);
  context.setImageInterpolation($.NSImageInterpolationHigh);
  const frame = $.NSMakeRect(100, 100, 824, 824);
  $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius(frame, 185, 185).addClip;
  source.drawInRectFromRectOperationFraction(frame,
    $.NSMakeRect(0, 0, source.size.width, source.size.height), $.NSCompositingOperationCopy, 1);
  context.flushGraphics;
  $.NSGraphicsContext.restoreGraphicsState;
  const png = bitmap.representationUsingTypeProperties($.NSPNGFileType, $({}));
  if (!png.writeToFileAtomically(args[1], true)) throw new Error('Cannot write app icon');
}
