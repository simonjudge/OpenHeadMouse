import AppKit
import CoreMotion
import Foundation

struct HelperConfiguration {
    var host = "127.0.0.1"
    var port: UInt16 = 5005

    static func parse(_ arguments: [String]) throws -> HelperConfiguration {
        var configuration = HelperConfiguration()
        var index = 0
        while index < arguments.count {
            let option = arguments[index]
            guard index + 1 < arguments.count else {
                throw ConfigurationError.missingValue(option)
            }
            let value = arguments[index + 1]
            switch option {
            case "--host":
                var address = in_addr()
                let isIPv4Address = value.withCString {
                    inet_pton(AF_INET, $0, &address)
                } == 1
                guard isIPv4Address else {
                    throw ConfigurationError.invalidHost
                }
                configuration.host = value
            case "--port":
                guard let port = UInt16(value), port > 0 else {
                    throw ConfigurationError.invalidPort(value)
                }
                configuration.port = port
            default:
                throw ConfigurationError.unknownOption(option)
            }
            index += 2
        }
        return configuration
    }

    enum ConfigurationError: Error, CustomStringConvertible {
        case missingValue(String)
        case invalidHost
        case invalidPort(String)
        case unknownOption(String)

        var description: String {
            switch self {
            case .missingValue(let option):
                return "Missing value for \(option)"
            case .invalidHost:
                return "Host must be a non-empty IPv4 address"
            case .invalidPort(let value):
                return "Port must be an integer from 1 to 65535 (got '\(value)')"
            case .unknownOption(let option):
                return "Unknown option '\(option)'"
            }
        }
    }
}

func debugPrint(_ msg: String) {
    fputs("[\(Date().formatted(date: .omitted, time: .standard))] \(msg)\n", stderr)
    fflush(stderr)
}

extension Double {
    func toDegrees() -> String {
        String(format: "%.1f", self * 180.0 / .pi)
    }
}

/// OpenHeadMouse AirPods Bridge: macOS app that streams CMHeadphoneMotionManager
/// quaternions as JSON over UDP to the configured IPv4 host and port.
///
/// HARDWARE REQUIREMENTS:
///   ⚠️  Only AirPods Pro (2nd gen) or AirPods Max support motion tracking
///   ❌ AirPods (4th gen), AirPods (3rd gen), AirPods Pro (1st gen) NOT supported
///
/// Usage:
///   1. Build: swift build
///   2. Run: ./.build/debug/OpenHeadMouseAirPodsHelper
///      Optional arguments: --host <IPv4-address> --port <1-65535>
///   3. Grant Microphone permission when prompted
///   4. Wear compatible AirPods and start head tracking
///   5. Python receiver will connect and begin receiving quaternion packets

@available(macOS 14.0, *)
class HeadMotionManager {
    private let motionManager = CMHeadphoneMotionManager()
    private let udpSocket: UDPSocket
    private var packetCount = 0
    var onStatusChanged: ((String) -> Void)?

    init(configuration: HelperConfiguration) {
        udpSocket = UDPSocket(host: configuration.host, port: configuration.port)
    }

    func start() throws {
        debugPrint("HeadMotionManager: starting headphone motion updates...")
        print("Starting headphone motion updates...")
        onStatusChanged?(
            motionManager.isDeviceMotionAvailable
                ? "Waiting for motion data"
                : "Waiting for compatible AirPods"
        )

        motionManager.startDeviceMotionUpdates(to: .main) { [weak self] motion, error in
            guard let self = self else {
                return
            }
            if let error = error {
                let message = "CoreMotion error: \(error.localizedDescription)"
                debugPrint("HeadMotionManager: \(message)")
                print("✗ \(message)")
                self.onStatusChanged?("Error: \(error.localizedDescription)")
                return
            }
            guard let motion = motion else {
                return
            }

            do {
                try self.sendQuaternion(attitude: motion.attitude)
                if self.packetCount == 1 {
                    self.onStatusChanged?("Streaming motion data")
                }
            } catch {
                debugPrint("HeadMotionManager: sendQuaternion error: \(error)")
                self.onStatusChanged?("UDP send error")
            }
        }

        debugPrint("HeadMotionManager: startDeviceMotionUpdates call completed")
        print("✓ Waiting for motion data from AirPods...")
    }

    func stop() {
        debugPrint("HeadMotionManager: stopping CoreMotion...")
        motionManager.stopDeviceMotionUpdates()
        udpSocket.close()
    }

    private func sendQuaternion(attitude: CMAttitude) throws {
        let q = attitude.quaternion
        let quat = [q.w, q.x, q.y, q.z]
        let norm = sqrt(quat.reduce(0) { $0 + $1 * $1 })
        let normalized = quat.map { $0 / norm }

        let packet: [String: Any] = [
            "timestamp": Date().timeIntervalSince1970,
            "quaternion": [
                "w": normalized[0],
                "x": normalized[1],
                "y": normalized[2],
                "z": normalized[3],
            ],
            "source": "airpods",
        ]

        let jsonData = try JSONSerialization.data(withJSONObject: packet)
        udpSocket.send(data: jsonData)
        packetCount += 1
        if packetCount % 30 == 0 {
            print("✓ Sent \(packetCount) packets. Latest: yaw=\(attitude.yaw.toDegrees())° pitch=\(attitude.pitch.toDegrees())° roll=\(attitude.roll.toDegrees())°")
        }
    }
}

/// Simple UDP socket wrapper for sending JSON packets.
class UDPSocket {
    private let host: String
    private let port: UInt16
    private var socket: Int32 = -1

    init(host: String, port: UInt16) {
        self.host = host
        self.port = port
        openSocket()
    }

    private func openSocket() {
        socket = Darwin.socket(AF_INET, SOCK_DGRAM, 0)
        guard socket != -1 else {
            print("Failed to create UDP socket")
            return
        }
    }

    func send(data: Data) {
        guard socket != -1 else { return }

        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        addr.sin_addr.s_addr = inet_addr(host)

        data.withUnsafeBytes { buffer in
            let ptr = buffer.baseAddress!.assumingMemoryBound(to: CChar.self)
            _ = withUnsafeBytes(of: &addr) { addrBuffer in
                Darwin.sendto(socket, ptr, data.count, 0, addrBuffer.baseAddress?.assumingMemoryBound(to: sockaddr.self), socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
    }

    func close() {
        if socket != -1 {
            Darwin.close(socket)
            socket = -1
        }
    }

    deinit {
        close()
    }
}

@available(macOS 14.0, *)
class AppDelegate: NSObject, NSApplicationDelegate {
    private let configuration: HelperConfiguration
    private var motionManager: HeadMotionManager?
    private var statusItem: NSStatusItem?
    private var statusMenuItem: NSMenuItem?

    init(configuration: HelperConfiguration) {
        self.configuration = configuration
        super.init()
    }

    func applicationDidFinishLaunching(_: Notification) {
        debugPrint("AppDelegate: applicationDidFinishLaunching called")

        // Create system tray icon
        debugPrint("AppDelegate: creating status bar item...")
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)

        if let button = statusItem?.button {
            if let image = NSImage(systemSymbolName: "headphones", accessibilityDescription: "OpenHeadMouse AirPods Helper") {
                image.isTemplate = true
                button.image = image
                button.imagePosition = .imageOnly
                debugPrint("AppDelegate: status bar button created with headphones symbol")
            } else {
                button.title = "HM"
                debugPrint("AppDelegate: status bar button created with HM fallback")
            }
            button.toolTip = "OpenHeadMouse AirPods Helper"
        }
        statusItem?.isVisible = true

        // Create menu
        let menu = NSMenu()
        let statusMenuItem = NSMenuItem(title: "Status: Starting", action: nil, keyEquivalent: "")
        self.statusMenuItem = statusMenuItem
        menu.addItem(statusMenuItem)
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(quitApp), keyEquivalent: "q"))
        statusItem?.menu = menu
        debugPrint("AppDelegate: menu created")

        // Start motion manager
        debugPrint("AppDelegate: creating HeadMotionManager...")
        motionManager = HeadMotionManager(configuration: configuration)
        motionManager?.onStatusChanged = { [weak self] status in
            self?.statusMenuItem?.title = "Status: \(status)"
        }

        debugPrint("AppDelegate: calling motionManager.start()...")
        do {
            try motionManager?.start()
            statusMenuItem.title = "Status: Waiting for AirPods"
            debugPrint("AppDelegate: motionManager started successfully")
            print("✓ AirPods CoreMotion helper started, streaming to \(configuration.host):\(configuration.port)")
        } catch {
            statusMenuItem.title = "Status: ✗ Error"
            debugPrint("AppDelegate: motionManager failed: \(error)")
            print("✗ Failed to start: \(error)")
        }
    }

    func applicationWillTerminate(_: Notification) {
        debugPrint("AppDelegate: applicationWillTerminate called")
        motionManager?.stop()
        print("AirPods helper stopped")
    }

    @objc func quitApp() {
        debugPrint("AppDelegate: quit requested")
        NSApplication.shared.terminate(nil)
    }
}

@available(macOS 14.0, *)
struct AirPodsHelper {
    static func main() {
        debugPrint("AirPodsHelper.main() starting...")

        let configuration: HelperConfiguration
        do {
            configuration = try HelperConfiguration.parse(
                Array(CommandLine.arguments.dropFirst())
            )
        } catch {
            fputs("Invalid AirPods helper arguments: \(error)\n", stderr)
            return
        }

        let app = NSApplication.shared
        debugPrint("NSApplication created")

        let delegate = AppDelegate(configuration: configuration)
        app.delegate = delegate
        debugPrint("AppDelegate set")

        app.setActivationPolicy(.accessory)
        debugPrint("Activation policy set")

        debugPrint("Entering run loop...")
        app.run()
        debugPrint("Run loop exited (should not reach here)")
    }
}

AirPodsHelper.main()
