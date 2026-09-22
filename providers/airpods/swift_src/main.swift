import AppKit
import CoreMotion
import Foundation

func debugPrint(_ msg: String) {
    fputs("[\(Date().formatted(date: .omitted, time: .standard))] \(msg)\n", stderr)
    fflush(stderr)
}

extension Double {
    func toDegrees() -> String {
        String(format: "%.1f", self * 180.0 / .pi)
    }
}

/// HeadMouse AirPods Bridge: macOS app that streams CMHeadphoneMotionManager
/// quaternions as JSON over UDP to 127.0.0.1:5005.
///
/// HARDWARE REQUIREMENTS:
///   ⚠️  Only AirPods Pro (2nd gen) or AirPods Max support motion tracking
///   ❌ AirPods (4th gen), AirPods (3rd gen), AirPods Pro (1st gen) NOT supported
///
/// Usage:
///   1. Build: swift build
///   2. Run: ./.build/debug/AirPodsHeadMouseHelper
///   3. Grant Microphone permission when prompted
///   4. Wear compatible AirPods and start head tracking
///   5. Python receiver will connect and begin receiving quaternion packets

@available(macOS 14.0, *)
class HeadMotionManager {
    private let motionManager = CMHeadphoneMotionManager()
    private let udpSocket = UDPSocket(host: "127.0.0.1", port: 5005)
    private var packetCount = 0

    func start() throws {
        debugPrint("HeadMotionManager: starting headphone motion updates...")
        print("Starting headphone motion updates...")

        // TEMPORARY: Comment out the actual startDeviceMotionUpdates to test
        motionManager.startDeviceMotionUpdates(to: .main) { motion, error in
            debugPrint("HeadMotionManager: callback fired")
            print("Callback fired!")
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
    private var motionManager: HeadMotionManager?
    private var statusItem: NSStatusItem?

    func applicationDidFinishLaunching(_: Notification) {
        debugPrint("AppDelegate: applicationDidFinishLaunching called")

        // Create system tray icon
        debugPrint("AppDelegate: creating status bar item...")
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

        if let button = statusItem?.button {
            button.title = "🎧"
            debugPrint("AppDelegate: status bar button created")
        }

        // Create menu
        let menu = NSMenu()
        let statusMenuItem = NSMenuItem(title: "Status: Starting", action: nil, keyEquivalent: "")
        menu.addItem(statusMenuItem)
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(quitApp), keyEquivalent: "q"))
        statusItem?.menu = menu
        debugPrint("AppDelegate: menu created")

        // Start motion manager
        debugPrint("AppDelegate: creating HeadMotionManager...")
        motionManager = HeadMotionManager()

        debugPrint("AppDelegate: calling motionManager.start()...")
        do {
            try motionManager?.start()
            statusMenuItem.title = "Status: ✓ Running"
            debugPrint("AppDelegate: motionManager started successfully")
            print("✓ AirPods CoreMotion helper started, streaming to 127.0.0.1:5005")
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

@main
@available(macOS 14.0, *)
struct AirPodsHelper {
    static func main() {
        debugPrint("AirPodsHelper.main() starting...")

        let app = NSApplication.shared
        debugPrint("NSApplication created")

        let delegate = AppDelegate()
        app.delegate = delegate
        debugPrint("AppDelegate set")

        app.setActivationPolicy(.accessory)
        debugPrint("Activation policy set")

        debugPrint("Entering run loop...")
        app.run()
        debugPrint("Run loop exited (should not reach here)")
    }
}
