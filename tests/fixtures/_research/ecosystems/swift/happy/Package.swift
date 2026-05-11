// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Sample",
    dependencies: [
        .package(name: "Alamofire", url: "https://github.com/Alamofire/Alamofire.git", from: "5.0.0"),
        .package(url: "https://github.com/apple/swift-nio.git", from: "2.0.0"),
    ],
    targets: []
)
