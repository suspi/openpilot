#!/usr/bin/env python3
import time
import cereal.messaging as messaging

def start_check():
  sm = messaging.SubMaster(['carState'])

  print("open a door")

  while True:
    sm.update()

    # Get interaction
    if sm.updated['carState']:

      isDoorOpen = sm['carState'].doorOpen

      if isDoorOpen:
          print("door is open")

if __name__ == "__main__":
  start_check()
