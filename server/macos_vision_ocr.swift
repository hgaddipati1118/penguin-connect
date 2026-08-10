import Foundation
import ImageIO
import Vision

guard CommandLine.arguments.count == 2 else {
    exit(2)
}

let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard
    let source = CGImageSourceCreateWithURL(imageURL as CFURL, nil),
    let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
else {
    exit(3)
}

let properties =
    CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
let orientationValue =
    (properties?[kCGImagePropertyOrientation] as? NSNumber)?.uint32Value ?? 1
let orientation = CGImagePropertyOrientation(rawValue: orientationValue) ?? .up

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.minimumTextHeight = 0.006

do {
    let handler = VNImageRequestHandler(
        cgImage: image,
        orientation: orientation,
        options: [:]
    )
    try handler.perform([request])
    let lines = (request.results ?? [])
        .sorted { left, right in
            let verticalDifference = left.boundingBox.midY - right.boundingBox.midY
            if abs(verticalDifference) > 0.012 {
                return verticalDifference > 0
            }
            return left.boundingBox.minX < right.boundingBox.minX
        }
        .compactMap { $0.topCandidates(1).first?.string }
        .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    FileHandle.standardOutput.write(
        lines.joined(separator: "\n").data(using: .utf8) ?? Data()
    )
} catch {
    exit(4)
}
