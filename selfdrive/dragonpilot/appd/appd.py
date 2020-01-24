#!/usr/bin/env python3
import time
import subprocess
import cereal
import cereal.messaging as messaging
ThermalStatus = cereal.log.ThermalData.ThermalStatus
from selfdrive.swaglog import cloudlog
from common.realtime import sec_since_boot
from common.params import Params, put_nonblocking
params = Params()

class App():

  # app type
  TYPE_GPS = 0
  TYPE_SERVICE = 1
  TYPE_GPS_SERVICE = 2
  TYPE_FULLSCREEN = 3
  TYPE_UTIL = 4

  # frame app
  FRAME = "ai.comma.plus.frame"
  FRAME_MAIN = ".MainActivity"

  # offroad app
  OFFROAD = "ai.comma.plus.offroad"
  OFFROAD_MAIN = ".MainActivity"

  # manual switch stats
  MANUAL_OFF = "-1"
  MANUAL_IDLE = "0"
  MANUAL_ON = "1"

  def appops_set(self, package, op, mode):
    self.system(f"LD_LIBRARY_PATH= appops set {package} {op} {mode}")

  def pm_grant(self, package, permission):
    self.system(f"pm grant {package} {permission}")

  def set_package_permissions(self):
    if self.permissions is not None:
      for permission in self.permissions:
        self.pm_grant(self.app, permission)
    if self.opts is not None:
      for opt in self.opts:
        self.appops_set(self.app, opt, "allow")

  def __init__(self, app, activity, enable_param, auto_run_param, manual_ctrl_param, app_type, permissions, opts):
    self.app = app
    # main activity
    self.activity = activity
    # read enable param
    self.enable_param = enable_param
    # read auto run param
    self.auto_run_param = auto_run_param
    # read manual run param
    self.manual_ctrl_param = manual_ctrl_param
    # if it's a service app, we do not kill if device is too hot
    # if it's a full screen app, we need to do extra process on frame/offroad
    self.app_type = app_type
    # app permissions
    self.permissions = permissions
    # app options
    self.opts = opts

    self.is_enabled = False
    self.last_is_enabled = False
    self.is_auto_runnable = False
    self.is_running = False
    self.manual_ctrl_status = self.MANUAL_IDLE
    self.manually_ctrled = False

    self.set_package_permissions()
    self.system("pm disable %s" % self.app)
    self.last_ts = sec_since_boot()

  def read_params(self):
    cur_time = sec_since_boot()
    if cur_time - self.last_ts > 5:
      self.last_is_enabled = self.is_enabled
      if self.enable_param is None:
        self.is_enabled = False
      else:
        self.is_enabled = True if params.get(self.enable_param, encoding='utf8') == "1" else False

      if self.is_enabled:
        # a service app should run automatically and not manual controllable.
        if self.app_type in [App.TYPE_SERVICE, App.TYPE_GPS_SERVICE]:
          self.is_auto_runnable = True
          self.manual_ctrl_status = self.MANUAL_IDLE
        else:
          if self.manual_ctrl_param is None:
            self.manual_ctrl_status = self.MANUAL_IDLE
          else:
            self.manual_ctrl_status = params.get(self.manual_ctrl_param, encoding='utf8')

          if self.auto_run_param is None:
            self.is_auto_runnable = False
          else:
            self.is_auto_runnable = True if params.get(self.auto_run_param, encoding='utf8') == "1" else False
      else:
        self.is_auto_runnable = False
        self.manual_ctrl_status = self.MANUAL_IDLE
        self.manually_ctrled = False

      self.last_ts = cur_time

  def run(self, force = False):
    if force or self.is_enabled:
      # app is manually ctrl, we record that
      if self.manual_ctrl_param is not None and self.manual_ctrl_status == self.MANUAL_ON:
        put_nonblocking(self.manual_ctrl_param, '0')
        self.manually_ctrled = True
        self.is_running = False

      # only run app if it's not running
      if force or not self.is_running:
        # if it's a full screen app, we need to stop frame and offroad to get keyboard access
        if self.app_type == self.TYPE_FULLSCREEN:
          self.system("pm disable %s" % self.FRAME)
          self.system("am start -n %s/%s" % (self.OFFROAD, self.OFFROAD_MAIN))

        self.system("pm enable %s" % self.app)

        if self.app_type == self.TYPE_GPS_SERVICE:
          self.appops_set(self.app, "android:mock_location", "allow")

        if self.app_type in [self.TYPE_SERVICE, self.TYPE_GPS_SERVICE]:
          self.system("am startservice %s/%s" % (self.app, self.activity))
        else:
          self.system("am start -n %s/%s" % (self.app, self.activity))
    self.is_running = True

  def kill(self, force = False):
    if force or self.is_enabled:
      # app is manually ctrl, we record that
      if self.manual_ctrl_param is not None and self.manual_ctrl_status == self.MANUAL_OFF:
        put_nonblocking(self.manual_ctrl_param, '0')
        self.manually_ctrled = True
        self.is_running = True

      # only kill app if it's running
      if force or self.is_running:
        # if it's a full screen app, we need to restart offroad and frame
        if self.app_type == self.TYPE_FULLSCREEN:
          self.system("pm disable %s" % self.OFFROAD)
          self.system("pm enable %s" % self.OFFROAD)
          self.system("pm enable %s" % self.FRAME)
          self.system("am start -n %s/%s" % (self.FRAME, self.FRAME_MAIN))

        if self.app_type == self.TYPE_GPS_SERVICE:
          self.appops_set(self.app, "android:mock_location", "deny")

        self.system("pkill %s" % self.app)
        self.is_running = False

  def system(self, cmd):
    try:
      subprocess.check_output(cmd, stderr=subprocess.STDOUT, shell=True)
    except subprocess.CalledProcessError as e:
      cloudlog.event("running failed",
                     cmd=e.cmd,
                     output=e.output[-1024:],
                     returncode=e.returncode)

def init_apps(apps):
  apps.append(App(
    # v1.16.2
    "com.tomtom.speedcams.android.map",
    "com.tomtom.speedcams.android.activities.SpeedCamActivity",
    "DragonEnableTomTom",
    "DragonBootTomTom",
    "DragonRunTomTom",
    App.TYPE_GPS,
    [
      "android.permission.ACCESS_FINE_LOCATION",
      "android.permission.ACCESS_COARSE_LOCATION",
      "android.permission.READ_EXTERNAL_STORAGE",
      "android.permission.WRITE_EXTERNAL_STORAGE",
    ],
    [
      "SYSTEM_ALERT_WINDOW",
    ]
  ))
  apps.append(App(
    # v4.3.0.600310 R2098NSLAE
    "com.autonavi.amapauto",
    "com.autonavi.amapauto.MainMapActivity",
    "DragonEnableAutonavi",
    "DragonBootAutonavi",
    "DragonRunAutonavi",
    App.TYPE_GPS,
    [
      "android.permission.ACCESS_FINE_LOCATION",
      "android.permission.ACCESS_COARSE_LOCATION",
      "android.permission.READ_EXTERNAL_STORAGE",
      "android.permission.WRITE_EXTERNAL_STORAGE",
    ],
    [
      "SYSTEM_ALERT_WINDOW",
    ]
  ))
  apps.append(App(
    # v6.40.3
    "com.mixplorer",
    "com.mixplorer.activities.BrowseActivity",
    "DragonEnableMixplorer",
    None,
    "DragonRunMixplorer",
    App.TYPE_UTIL,
    [
      "android.permission.READ_EXTERNAL_STORAGE",
      "android.permission.WRITE_EXTERNAL_STORAGE",
    ],
    [],
  ))
  apps.append(App(
    # v2.9.5 build 74
    "tw.com.ainvest.outpack",
    "tw.com.ainvest.outpack.ui.MainActivity",
    "DragonEnableAegis",
    "DragonBootAegis",
    "DragonRunAegis",
    App.TYPE_GPS,
    [
      "android.permission.ACCESS_FINE_LOCATION",
      "android.permission.READ_EXTERNAL_STORAGE",
      "android.permission.WRITE_EXTERNAL_STORAGE",
    ],
    [
      "SYSTEM_ALERT_WINDOW",
    ]
  ))
  apps.append(App(
    "cn.dragonpilot.gpsservice",
    "cn.dragonpilot.gpsservice.MainService",
    "DragonGreyPandaMode",
    None,
    None,
    App.TYPE_GPS_SERVICE,
    [],
    [],
  ))
  apps.append(App(
    # v4.57.2.0
    "com.waze",
    "com.waze.MainActivity",
    "DragonWazeMode",
    None,
    "DragonRunWaze",
    App.TYPE_FULLSCREEN,
    [
      "android.permission.ACCESS_FINE_LOCATION",
      "android.permission.ACCESS_COARSE_LOCATION",
      "android.permission.READ_EXTERNAL_STORAGE",
      "android.permission.WRITE_EXTERNAL_STORAGE",
      "android.permission.RECORD_AUDIO",
    ],
    [],
  ))

def main():
  apps = []

  # enable hotspot on boot
  if params.get("DragonBootHotspot", encoding='utf8') == "1":
    system(f"settings put system accelerometer_rotation 0")
    system(f"settings put system user_rotation 1")
    system(f"pm enable com.android.settings")
    system(f"am start -n com.android.settings/.TetherSettings")
    time.sleep(1)
    system(f"LD_LIBRARY_PATH= input tap 995 160")
    system(f"pkill com.android.settings")

  init_apps(apps)

  last_started = False
  thermal_sock = messaging.sub_sock('thermal')

  frame = 0
  start_delay = None
  stop_delay = None
  allow_auto_run = True
  last_thermal_status = None
  thermal_status = None

  set_location_provider_allowed = False

  while 1: #has_enabled_apps:
    has_fullscreen_apps = False
    has_gps_apps = False
    has_gps_service_apps = False

    for app in apps:
      # read params loop
      app.read_params()
      if app.last_is_enabled and not app.is_enabled and app.is_running:
        app.kill(True)

      if app.is_enabled:
        if not has_fullscreen_apps and app.app_type == App.TYPE_FULLSCREEN:
          has_fullscreen_apps = True
        elif not has_gps_apps and app.app_type == App.TYPE_GPS:
          has_gps_apps = True
        elif not has_gps_service_apps and app.app_type == App.TYPE_GPS_SERVICE:
          has_gps_service_apps = True

        # process manual ctrl apps
        if app.manual_ctrl_status != App.MANUAL_IDLE:
          if app.manual_ctrl_status == App.MANUAL_ON:
            app.run(True)
          else:
            app.kill(True)

    # set location provider accuracy
    if not set_location_provider_allowed and (has_gps_apps or has_gps_service_apps):
      system(f"settings put secure location_providers_allowed -gps")
      system(f"settings put secure location_providers_allowed -network")
      system(f"settings put secure location_providers_allowed +gps,network")
      set_location_provider_allowed = True

    msg = messaging.recv_sock(thermal_sock, wait=True)
    started = msg.thermal.started
    # when car is running
    if started:
      stop_delay = None
      # apps start 5 secs later
      if start_delay is None:
        start_delay = frame + 5

      thermal_status = msg.thermal.thermalStatus
      if thermal_status <= ThermalStatus.yellow:
        allow_auto_run = True
        # when temp reduce from red to yellow, we add start up delay as well
        # so apps will not start up immediately
        if last_thermal_status == ThermalStatus.red:
          start_delay = frame + 60
      elif thermal_status >= ThermalStatus.red:
        allow_auto_run = False

      last_thermal_status = thermal_status

      # we run service apps and kill all util apps
      # only run once
      if last_started != started:
        for app in apps:
          if app.app_type in [App.TYPE_SERVICE, App.TYPE_GPS_SERVICE]:
            app.run()
          elif app.app_type == App.TYPE_UTIL:
            app.kill()

      # only run apps that's not manually ctrled
      for app in apps:
        if not app.manually_ctrled:
          if has_fullscreen_apps:
            if app.app_type == App.TYPE_FULLSCREEN:
              app.run()
            elif app.app_type in [App.TYPE_GPS, App.TYPE_UTIL]:
              app.kill()
          else:
            if not allow_auto_run:
              app.kill()
            else:
              if frame > start_delay and app.is_auto_runnable and app.app_type == App.TYPE_GPS:
                app.run()
    # when car is stopped
    else:
      start_delay = None
      # set delay to 30 seconds
      if stop_delay is None:
        stop_delay = frame + 30

      for app in apps:
        if app.is_running and not app.manually_ctrled:
          if has_fullscreen_apps or frame > stop_delay:
            app.kill()

    if last_started != started:
      for app in apps:
        app.manually_ctrled = False

    last_started = started
    frame += 3
    time.sleep(3)

def system(cmd):
  try:
    cloudlog.info("running %s" % cmd)
    subprocess.check_output(cmd, stderr=subprocess.STDOUT, shell=True)
  except subprocess.CalledProcessError as e:
    cloudlog.event("running failed",
                   cmd=e.cmd,
                   output=e.output[-1024:],
                   returncode=e.returncode)

if __name__ == "__main__":
  main()
