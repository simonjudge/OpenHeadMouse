"""Test orientation mapping: perform specific head movements and check reported angles.

Run this with your desired provider active. Instructions:
1. Point your head straight ahead (neutral position, calibrate)
2. Turn head LEFT (yaw left) - should report POSITIVE yaw
3. Nod DOWN (pitch down) - should report NEGATIVE pitch
4. Nod UP (pitch up) - should report POSITIVE pitch
5. Tilt LEFT (roll left, left ear down) - should report POSITIVE roll
6. Tilt RIGHT (roll right, right ear down) - should report NEGATIVE roll

Expected signs per spec (reference frame: +X forward, +Y left, +Z up):
  - Yaw left (turn left): POSITIVE
  - Pitch up (nod up): POSITIVE
  - Roll left (left ear down): POSITIVE
"""

import sys
import time
from core import quaternion_math as qm

def run_test(provider_instance, duration_sec=10, sample_rate_hz=30):
    """Run provider and print Euler angles."""
    print(f"\nStarting orientation test for {provider_instance.__class__.__name__}")
    print("=" * 70)
    print("Perform head movements and observe the Yaw/Pitch/Roll values below.")
    print(f"Test duration: {duration_sec} seconds at ~{sample_rate_hz} Hz\n")
    
    provider_instance.start()
    start_time = time.monotonic()
    sample_interval = 1.0 / sample_rate_hz
    last_print = start_time
    neutral_quaternion = None
    
    try:
        while time.monotonic() - start_time < duration_sec:
            try:
                sample = provider_instance.output_queue.get(timeout=0.1)
                if sample is None:
                    continue
                
                # Capture neutral quaternion on first frame
                if neutral_quaternion is None:
                    neutral_quaternion = sample.quaternion
                    print("Captured neutral position. Now perform head movements...\n")
                    print("Yaw°      Pitch°     Roll°")
                    print("─" * 40)
                
                # Extract relative Euler angles
                q_rel = qm.relative_rotation(neutral_quaternion, sample.quaternion)
                yaw, pitch, roll = qm.to_euler_zyx(q_rel)
                yaw_deg = float(yaw) * 180 / 3.14159265
                pitch_deg = float(pitch) * 180 / 3.14159265
                roll_deg = float(roll) * 180 / 3.14159265
                
                # Print at controlled rate
                now = time.monotonic()
                if now - last_print >= sample_interval:
                    print(f"{yaw_deg:+7.1f}   {pitch_deg:+7.1f}   {roll_deg:+7.1f}")
                    last_print = now
                    
            except Exception:
                continue
                
    finally:
        provider_instance.stop()
        print("\nTest complete.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Test head orientation mapping")
    parser.add_argument("--provider", choices=["uniface", "ximu3", "airpods"], 
                       default="uniface", help="Which provider to test")
    parser.add_argument("--duration", type=int, default=15, 
                       help="Test duration in seconds")
    
    args = parser.parse_args()
    
    # Load config and create provider
    from main import load_config, make_provider
    config = load_config()
    
    if args.provider == "ximu3":
        # xIMU3 needs discovery
        from providers.ximu3_provider import discover_ximu3_devices, Ximu3Provider
        print("Discovering xIMU3 devices...")
        devices = discover_ximu3_devices()
        if not devices:
            print("No xIMU3 devices found!")
            sys.exit(1)
        device = devices[0]
        print(f"Using: {device.name}")
        provider = Ximu3Provider(
            connection_type=device.connection_type,
            port_name=device.port_name,
            wifi_ip_address=device.ip_address,
            wifi_port_send=9000,
            wifi_port_receive=8001,
            axes_alignment=config.get("providers", {}).get("ximu3", {}).get("axes_alignment", "+X-Z+Y"),
            ahrs_axes_convention=config.get("providers", {}).get("ximu3", {}).get("ahrs_axes_convention", 2),
            target_rate_hz=config.get("providers", {}).get("ximu3", {}).get("target_rate_hz", 200),
        )
    else:
        provider = make_provider(args.provider, config)
    
    run_test(provider, duration_sec=args.duration)
