#include <ArduinoBLE.h>
#include <LSM6DS3.h>
#include <Wire.h>
#include <stdint.h>

namespace {

constexpr char kLocalName[] = "OpenHeadMouse-XIAO";
constexpr char kServiceUuid[] = "b28a0001-7f2d-4c9a-9b23-1d43c6e8a501";
constexpr char kSensorUuid[] = "b28a0002-7f2d-4c9a-9b23-1d43c6e8a501";
constexpr uint32_t kSamplePeriodUs = 10000;

LSM6DS3 imu(I2C_MODE, 0x6A);
BLEService sensorService(kServiceUuid);
BLECharacteristic sensorCharacteristic(kSensorUuid, BLENotify, 20);
uint32_t nextSampleUs = 0;

#pragma pack(push, 1)
struct SensorPacket {
  uint32_t timestampUs;
  int16_t accelMg[3];
  int16_t gyroCentiDps[3];
};
#pragma pack(pop)

static_assert(sizeof(SensorPacket) == 16, "Unexpected sensor packet layout");

int16_t toInt16(float value) {
  long rounded = lroundf(value);
  if (rounded > INT16_MAX) rounded = INT16_MAX;
  if (rounded < INT16_MIN) rounded = INT16_MIN;
  return static_cast<int16_t>(rounded);
}

void fail(const char* message) {
  Serial.println(message);
  while (true) delay(1000);
}

}  // namespace

void setup() {
  Serial.begin(115200);
  Wire.begin();

  if (imu.begin() != 0) fail("Could not initialize the XIAO Sense LSM6DS3TR-C");
  if (!BLE.begin()) fail("Could not initialize Bluetooth LE");

  sensorService.addCharacteristic(sensorCharacteristic);
  BLE.setLocalName(kLocalName);
  BLE.setDeviceName(kLocalName);
  BLE.setAdvertisedService(sensorService);
  BLE.addService(sensorService);
  BLE.advertise();
  nextSampleUs = micros();
  Serial.println("OpenHeadMouse XIAO sensor advertising");
}

void loop() {
  BLE.poll();
  if (!BLE.connected()) {
    delay(5);
    return;
  }

  const uint32_t nowUs = micros();
  if (static_cast<int32_t>(nowUs - nextSampleUs) < 0) return;
  nextSampleUs = nowUs + kSamplePeriodUs;

  SensorPacket packet{
      nowUs,
      {
          toInt16(imu.readFloatAccelX() * 1000.0f),
          toInt16(imu.readFloatAccelY() * 1000.0f),
          toInt16(imu.readFloatAccelZ() * 1000.0f),
      },
      {
          toInt16(imu.readFloatGyroX() * 100.0f),
          toInt16(imu.readFloatGyroY() * 100.0f),
          toInt16(imu.readFloatGyroZ() * 100.0f),
      },
  };
  sensorCharacteristic.writeValue(
      reinterpret_cast<const uint8_t*>(&packet), sizeof(packet));
}
