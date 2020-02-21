from cereal import car
from common.numpy_fast import mean
from common.kalman.simple_kalman import KF1D
from opendbc.can.can_define import CANDefine
from opendbc.can.parser import CANParser
from selfdrive.config import Conversions as CV
from selfdrive.car.toyota.values import CAR, DBC, STEER_THRESHOLD, TSS2_CAR, NO_DSU_CAR, HD_STEER_SENSOR_CAR

from common.realtime import sec_since_boot
from common.params import Params
params = Params()

GearShifter = car.CarState.GearShifter

def parse_gear_shifter(gear):
  return {'P': GearShifter.park, 'R': GearShifter.reverse, 'N': GearShifter.neutral,
              'D': GearShifter.drive, 'B': GearShifter.brake}.get(gear, GearShifter.unknown)


def get_can_parser(CP):

  signals = [
    # sig_name, sig_address, default
    ("STEER_ANGLE", "STEER_ANGLE_SENSOR", 0),
    ("GEAR", "GEAR_PACKET", 0),
    ("BRAKE_PRESSED", "BRAKE_MODULE", 0),
    ("WHEEL_SPEED_FL", "WHEEL_SPEEDS", 0),
    ("WHEEL_SPEED_FR", "WHEEL_SPEEDS", 0),
    ("WHEEL_SPEED_RL", "WHEEL_SPEEDS", 0),
    ("WHEEL_SPEED_RR", "WHEEL_SPEEDS", 0),
    ("DOOR_OPEN_FL", "SEATS_DOORS", 1),
    ("DOOR_OPEN_FR", "SEATS_DOORS", 1),
    ("DOOR_OPEN_RL", "SEATS_DOORS", 1),
    ("DOOR_OPEN_RR", "SEATS_DOORS", 1),
    ("SEATBELT_DRIVER_UNLATCHED", "SEATS_DOORS", 1),
    ("TC_DISABLED", "ESP_CONTROL", 1),
    ("STEER_FRACTION", "STEER_ANGLE_SENSOR", 0),
    ("STEER_RATE", "STEER_ANGLE_SENSOR", 0),
    ("CRUISE_ACTIVE", "PCM_CRUISE", 0),
    ("CRUISE_STATE", "PCM_CRUISE", 0),
    ("STEER_TORQUE_DRIVER", "STEER_TORQUE_SENSOR", 0),
    ("STEER_TORQUE_EPS", "STEER_TORQUE_SENSOR", 0),
    ("TURN_SIGNALS", "STEERING_LEVERS", 3),   # 3 is no blinkers
    ("LKA_STATE", "EPS_STATUS", 0),
    ("IPAS_STATE", "EPS_STATUS", 1),
    ("BRAKE_LIGHTS_ACC", "ESP_CONTROL", 0),
  ]

  checks = [
    ("WHEEL_SPEEDS", 80),
    ("STEER_ANGLE_SENSOR", 80),
    ("PCM_CRUISE", 33),
    ("STEER_TORQUE_SENSOR", 50),
    ("EPS_STATUS", 25),
  ]

  if CP.carFingerprint in [CAR.LEXUS_ISH, CAR.LEXUS_GSH]:
    signals.append(("GAS_PEDAL", "GAS_PEDAL_ALT", 0))
    signals.append(("MAIN_ON", "PCM_CRUISE_ALT", 0))
    signals.append(("SET_SPEED", "PCM_CRUISE_ALT", 0))
    signals.append(("AUTO_HIGH_BEAM", "LIGHT_STALK_ISH", 0))
    checks += [
      ("BRAKE_MODULE", 50),
      ("GAS_PEDAL_ALT", 50),
      ("PCM_CRUISE_ALT", 1),
    ]
  else:
    signals += [
      ("AUTO_HIGH_BEAM", "LIGHT_STALK", 0),
      ("GAS_PEDAL", "GAS_PEDAL", 0),
    ]
    checks += [
      ("BRAKE_MODULE", 40),
      ("GAS_PEDAL", 33),
    ]

  if CP.carFingerprint == CAR.LEXUS_IS:
    signals.append(("MAIN_ON", "DSU_CRUISE", 0))
    signals.append(("SET_SPEED", "DSU_CRUISE", 0))
    checks.append(("DSU_CRUISE", 5))
  else:
    signals.append(("MAIN_ON", "PCM_CRUISE_2", 0))
    signals.append(("SET_SPEED", "PCM_CRUISE_2", 0))
    signals.append(("LOW_SPEED_LOCKOUT", "PCM_CRUISE_2", 0))
    checks.append(("PCM_CRUISE_2", 33))

  if CP.carFingerprint in NO_DSU_CAR or HD_STEER_SENSOR_CAR or CP.carFingerprint == CAR.LEXUS_ISH:
    signals += [("STEER_ANGLE", "STEER_TORQUE_SENSOR", 0)]

  if CP.carFingerprint == CAR.PRIUS or CAR.PRIUS_2019:
    signals += [("STATE", "AUTOPARK_STATUS", 0)]

  # add gas interceptor reading if we are using it
  if CP.enableGasInterceptor:
    signals.append(("INTERCEPTOR_GAS", "GAS_SENSOR", 0))
    signals.append(("INTERCEPTOR_GAS2", "GAS_SENSOR", 0))
    checks.append(("GAS_SENSOR", 50))

  checks = []

  return CANParser(DBC[CP.carFingerprint]['pt'], signals, checks, 0)


def get_cam_can_parser(CP):

  signals = [("FORCE", "PRE_COLLISION", 0), ("PRECOLLISION_ACTIVE", "PRE_COLLISION", 0)]

  # use steering message to check if panda is connected to frc
  checks = [("STEERING_LKA", 42)]

  return CANParser(DBC[CP.carFingerprint]['pt'], signals, checks, 2)


class CarState():
  def __init__(self, CP):

    self.CP = CP
    self.can_define = CANDefine(DBC[CP.carFingerprint]['pt'])
    self.shifter_values = self.can_define.dv["GEAR_PACKET"]['GEAR']
    self.left_blinker_on = 0
    self.right_blinker_on = 0
    self.angle_offset = 0.
    self.init_angle_offset = False

    # initialize can parser
    self.car_fingerprint = CP.carFingerprint

    # vEgo kalman filter
    dt = 0.01
    # Q = np.matrix([[10.0, 0.0], [0.0, 100.0]])
    # R = 1e3
    self.v_ego_kf = KF1D(x0=[[0.0], [0.0]],
                         A=[[1.0, dt], [0.0, 1.0]],
                         C=[1.0, 0.0],
                         K=[[0.12287673], [0.29666309]])
    self.v_ego = 0.0

    # dragonpilot
    self.dragon_toyota_stock_dsu = False
    self.ts_last_check = 0.

  def update(self, cp, cp_cam):
    # dragonpilot, don't check for param too often as it's a kernel call
    ts = sec_since_boot()
    if ts - self.ts_last_check > 5.:
      self.dragon_toyota_stock_dsu = True if params.get("DragonToyotaStockDSU", encoding='utf8') == "1" else False
      self.ts_last_check = ts

    # update prevs, update must run once per loop
    self.prev_left_blinker_on = self.left_blinker_on
    self.prev_right_blinker_on = self.right_blinker_on

    self.door_all_closed = not any([cp.vl["SEATS_DOORS"]['DOOR_OPEN_FL'], cp.vl["SEATS_DOORS"]['DOOR_OPEN_FR'],
                                    cp.vl["SEATS_DOORS"]['DOOR_OPEN_RL'], cp.vl["SEATS_DOORS"]['DOOR_OPEN_RR']])
    self.seatbelt = not cp.vl["SEATS_DOORS"]['SEATBELT_DRIVER_UNLATCHED']

    self.brake_pressed = cp.vl["BRAKE_MODULE"]['BRAKE_PRESSED']
    if self.CP.enableGasInterceptor:
      self.pedal_gas = (cp.vl["GAS_SENSOR"]['INTERCEPTOR_GAS'] + cp.vl["GAS_SENSOR"]['INTERCEPTOR_GAS2']) / 2.
    elif self.CP.carFingerprint in [CAR.LEXUS_ISH, CAR.LEXUS_GSH]:
      self.pedal_gas = cp.vl["GAS_PEDAL_ALT"]['GAS_PEDAL']
    else:
      self.pedal_gas = cp.vl["GAS_PEDAL"]['GAS_PEDAL']
    self.car_gas = self.pedal_gas
    self.esp_disabled = cp.vl["ESP_CONTROL"]['TC_DISABLED']

    # calc best v_ego estimate, by averaging two opposite corners
    self.v_wheel_fl = cp.vl["WHEEL_SPEEDS"]['WHEEL_SPEED_FL'] * CV.KPH_TO_MS
    self.v_wheel_fr = cp.vl["WHEEL_SPEEDS"]['WHEEL_SPEED_FR'] * CV.KPH_TO_MS
    self.v_wheel_rl = cp.vl["WHEEL_SPEEDS"]['WHEEL_SPEED_RL'] * CV.KPH_TO_MS
    self.v_wheel_rr = cp.vl["WHEEL_SPEEDS"]['WHEEL_SPEED_RR'] * CV.KPH_TO_MS
    v_wheel = mean([self.v_wheel_fl, self.v_wheel_fr, self.v_wheel_rl, self.v_wheel_rr])

    # Kalman filter
    if abs(v_wheel - self.v_ego) > 2.0:  # Prevent large accelerations when car starts at non zero speed
      self.v_ego_kf.x = [[v_wheel], [0.0]]

    self.v_ego_raw = v_wheel
    v_ego_x = self.v_ego_kf.update(v_wheel)
    self.v_ego = float(v_ego_x[0])
    self.a_ego = float(v_ego_x[1])
    self.standstill = not v_wheel > 0.001

    if self.CP.carFingerprint in TSS2_CAR:
      self.angle_steers = cp.vl["STEER_TORQUE_SENSOR"]['STEER_ANGLE']

    elif self.CP.carFingerprint in NO_DSU_CAR or HD_STEER_SENSOR_CAR or self.CP.carFingerprint == CAR.LEXUS_ISH:

      # cp.vl["STEER_TORQUE_SENSOR"]['STEER_ANGLE'] is zeroed to where the steering angle is at start.
      # need to apply an offset as soon as the steering angle measurements are both received
      self.angle_steers = cp.vl["STEER_TORQUE_SENSOR"]['STEER_ANGLE'] - self.angle_offset
      angle_wheel = cp.vl["STEER_ANGLE_SENSOR"]['STEER_ANGLE'] + cp.vl["STEER_ANGLE_SENSOR"]['STEER_FRACTION']
      if abs(angle_wheel) > 1e-3 and abs(self.angle_steers) > 1e-3 and not self.init_angle_offset:
        self.init_angle_offset = True
        self.angle_offset = self.angle_steers - angle_wheel
    else:
      self.angle_steers = cp.vl["STEER_ANGLE_SENSOR"]['STEER_ANGLE'] + cp.vl["STEER_ANGLE_SENSOR"]['STEER_FRACTION']
    self.angle_steers_rate = cp.vl["STEER_ANGLE_SENSOR"]['STEER_RATE']
    can_gear = int(cp.vl["GEAR_PACKET"]['GEAR'])
    self.gear_shifter = parse_gear_shifter(self.shifter_values.get(can_gear, None))
    if self.CP.carFingerprint == CAR.LEXUS_IS:
      self.main_on = cp.vl["DSU_CRUISE"]['MAIN_ON']
    elif self.CP.carFingerprint in [CAR.LEXUS_ISH, CAR.LEXUS_GSH]:
      self.main_on = cp.vl["PCM_CRUISE_ALT"]['MAIN_ON']
    else:
      self.main_on = cp.vl["PCM_CRUISE_2"]['MAIN_ON']
    self.left_blinker_on = cp.vl["STEERING_LEVERS"]['TURN_SIGNALS'] == 1
    self.right_blinker_on = cp.vl["STEERING_LEVERS"]['TURN_SIGNALS'] == 2

    # 2 is standby, 10 is active. TODO: check that everything else is really a faulty state
    self.steer_state = cp.vl["EPS_STATUS"]['LKA_STATE']
    self.steer_error = cp.vl["EPS_STATUS"]['LKA_STATE'] not in [1, 5]
    self.ipas_active = cp.vl['EPS_STATUS']['IPAS_STATE'] == 3
    self.brake_error = 0
    self.steer_torque_driver = cp.vl["STEER_TORQUE_SENSOR"]['STEER_TORQUE_DRIVER']
    self.steer_torque_motor = cp.vl["STEER_TORQUE_SENSOR"]['STEER_TORQUE_EPS']
    # we could use the override bit from dbc, but it's triggered at too high torque values
    self.steer_override = abs(self.steer_torque_driver) > STEER_THRESHOLD

    self.user_brake = 0
    if self.CP.carFingerprint == CAR.LEXUS_IS:
      self.v_cruise_pcm = cp.vl["DSU_CRUISE"]['SET_SPEED']
      self.low_speed_lockout = False
    elif self.CP.carFingerprint in [CAR.LEXUS_ISH, CAR.LEXUS_GSH]:
      self.v_cruise_pcm = cp.vl["PCM_CRUISE_ALT"]['SET_SPEED']
      self.low_speed_lockout = False
    else:
      self.v_cruise_pcm = cp.vl["PCM_CRUISE_2"]['SET_SPEED']
      self.low_speed_lockout = cp.vl["PCM_CRUISE_2"]['LOW_SPEED_LOCKOUT'] == 2
    if self.CP.carFingerprint in [CAR.LEXUS_ISH, CAR.LEXUS_GSH]:
      # Lexus ISH does not have curise status value (always 0), so we use curise_active value instead
      self.pcm_acc_status = cp.vl["PCM_CRUISE"]['CRUISE_ACTIVE']
    else:
      self.pcm_acc_status = cp.vl["PCM_CRUISE"]['CRUISE_STATE']
    self.pcm_acc_active = bool(cp.vl["PCM_CRUISE"]['CRUISE_ACTIVE'])
    self.brake_lights = bool(cp.vl["ESP_CONTROL"]['BRAKE_LIGHTS_ACC'] or self.brake_pressed)
    if self.CP.carFingerprint == CAR.PRIUS or CAR.PRIUS_2019:
      self.generic_toggle = cp.vl["AUTOPARK_STATUS"]['STATE'] != 0
    elif self.CP.carFingerprint in [CAR.LEXUS_ISH, CAR.LEXUS_GSH]:
      self.generic_toggle = bool(cp.vl["LIGHT_STALK_ISH"]['AUTO_HIGH_BEAM'])
    else:
      self.generic_toggle = bool(cp.vl["LIGHT_STALK"]['AUTO_HIGH_BEAM'])

    self.stock_aeb = bool(cp_cam.vl["PRE_COLLISION"]["PRECOLLISION_ACTIVE"] and cp_cam.vl["PRE_COLLISION"]["FORCE"] < -1e-5)

    if self.dragon_toyota_stock_dsu and self.generic_toggle and self.main_on:
      enable_acc = True
      if not self.gear_shifter == GearShifter.drive or not self.seatbelt or not self.door_all_closed:
       enable_acc = False
      self.pcm_acc_active = enable_acc
      if self.standstill:
        self.pcm_acc_status = 7
      else:
        self.pcm_acc_status = 1