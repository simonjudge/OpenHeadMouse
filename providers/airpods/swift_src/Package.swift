// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AirPodsHeadMouseHelper",
    platforms: [
        .macOS(.v14)
    ],
    targets: [
        .executableTarget(
            name: "AirPodsHeadMouseHelper",
            dependencies: [],
            path: ".",
            exclude: ["BUILD.md", "Info.plist"],
            linkerSettings: [
                .unsafeFlags([
                    "-Xlinker", "-sectcreate",
                    "-Xlinker", "__TEXT",
                    "-Xlinker", "__info_plist",
                    "-Xlinker", "Info.plist",
                ])
            ]
        )
    ]
)
