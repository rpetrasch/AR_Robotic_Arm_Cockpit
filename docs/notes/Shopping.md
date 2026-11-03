# Upgrades & extensions

## 1. Teensy 3.5 → 4.1 upgrade

Annin has no official AR3→Teensy 4.1 retrofit guide. The AR3 used a Teensy 3.5, and the AR4 moved to integrated-encoder motors and a Teensy 4.1. So this is a retrofit you do yourself, using the Annin documents plus one community precedent.

**References**
- **anninrobotics.com → Downloads:** AR4 manual, wiring diagrams and Teensy sketch, plus the AR3 manual for your current pinout.
- **ar4_ros_driver (ycheng517):** it ships its own Teensy and Nano sketches for the ROS protocol, flashed the same way as in Annin's AR4 setup. **Flash these, not Annin's ARCS sketch.** ARCS won't talk to the arm afterwards unless you re-flash.
- **tran-nick/AR3_ROS2:** an AR3 retrofitted with a Teensy 4.1, including a sketch that runs on both 3.5 and 4.1. Good for checking pin mapping.

**Gotcha: 3.3 V logic.** The Teensy 3.5 accepts 5 V on its inputs; the Teensy 4.1 does not (3.3 V max). Your AMT102 encoders and limit switches are probably 5 V and will damage the 4.1 if wired directly.
- For inputs, use a 74LVC245 buffer powered at 3.3 V (its inputs tolerate 5 V) or resistor dividers.
- STEP/DIR outputs at 3.3 V usually drive optocoupled stepper drivers fine. Check the drivers' minimum input current; if it's marginal, buffer through a 74HCT245.

**Steps**
1. **Document the current arm before touching anything.** Photograph the wiring and note every Teensy 3.5 pin and its function. Export the ARCS calibration values: steps/deg, encoder multipliers, joint directions, limits and calibration offsets.
2. **Buy parts:** a Teensy 4.1 (the Ethernet version isn't needed), an Arduino Nano and an ACS712 current sensor for the gripper, plus the level shifters.
3. **Choose a pin map.** Rewire to match the pin defines in the ROS sketch (preferred: no firmware edits), or edit the defines to match your wiring.
4. **Set AR3 constants** in the sketch and the driver config, replacing the AR4 values: steps/deg, encoder ratios, directions and limits.
5. **Flash** with Arduino IDE 2 + Teensyduino.
6. **Bench-test one joint at a time** with motor power at low current: jog it, check direction and encoder counts, then check each limit switch.
7. **Bring up in ROS:** `ros2 launch ar_hardware_interface ar_hardware.launch.py calibrate:=True`. Confirm the RViz pose matches the real arm before commanding any motion, and start at 10–20 % speed.

## 2. GPUs

For the research topics, a more powerful workstation with > 16 GB GPU RAM is necessary, e.g. RTX 5090, AMD Ryzen AI Halo / Ryzen AI Max+ 395, NVIDIA DGX Spark. 


## 3. Hardware e-stop

**Design: two channels and fail-safe.** A broken wire must stop the arm.

1. **Button.** Check your red button: it should latch (twist or pull to release) and have NC (normally closed) contacts. Ideally it has two NC contact blocks; they cost little to add if it doesn't.
2. **Channel 1, power cut.** Wire NC contact 1 in series with the coil of a DC-rated contactor or relay that switches the **motor supply** to the stepper drivers. Don't run the motor current through the button itself, because DC arcs.
3. **Keep the logic supply on.** The Teensy, encoders and 5 V rail stay on a separate supply, so the encoders keep counting if the arm moves while stopped.
4. **Channel 2, signal.** Wire NC contact 2 between the Teensy e-stop input and GND, with a pull-up to 3.3 V. Open circuit means stopped. Find the pin in the ROS Teensy sketch (search for `ESTOP`). The driver already exposes `isEStopped` / `resetEStop`.
5. **Reset behaviour.** Releasing the button must never resume motion. Motion only restarts after an explicit software reset (the `ResetEstop` service in the spec), and after re-homing if the arm moved.
6. **Gravity.** Cutting motor power removes holding torque, so joints 2 and 3 may sag or drop. Test this with the arm in a low pose and nothing under it. If they drop, keep a payload-free zone, and consider brakes later.
7. **Test before every session.** Press the button mid-motion and confirm the motors de-energize, `/safety/status` shows the stop, and nothing moves after release until the software reset.

If the motor supply is fed from mains, have the mains side wired by someone qualified. Everything after the power supply is low-voltage DC.

## 4. Depth camera

Planned. The spec was umdated, so perception works with the webcams now (assuming a flat table) and switches to depth through a config flag. We need to decice about the model; RealSense and OAK-D use different ROS packages.

## 5. Pneumatic gripper

Hardware for the gripper is already bought. Some parts are missing, e.g. air compressor.

### Air Supply Requirements

- Operating Pressure: The standard SMC gripper used in the AR4 build manual operates within a range of 22 to 102 psi. For most applications, running it around 80 psi is ideal for optimal grip strength. [1] (https://anninrobotics.com/community/questions/pneumatic-gripper)
- Compressor Options:
	- Small Workshop Compressor: The robot creator recommends a standard, budget-friendly 2 to 3-gallon portable air compressor (such as a pancake or oilless compressor from Harbor Freight or local hardware stores).
	- Silent/Airbrush Compressor: If you are operating the robot in a bedroom or quiet office, community members frequently use small airbrush compressors with a small tank (around 3 liters). They are exceptionally quiet, though they may cycle more often and limit your maximum gripping force if they cannot reach higher pressures (https://anninrobotics.com/community/questions/air-compressors, https://orcaslicer.cn/upload/news/file/20241230/20241230151926_37001.pdf, https://anninrobotics.com/community/questions/air-compressor, https://anninrobotics.com/forum/questions/air-compressor/, https://anninrobotics.com/community/questions/pneumatic-gripper)

### Complete Pneumatic Setup Checklist

To make the pneumatic gripper work with your AR4 controller, the compressor is only one part of the loop. You will also need:
1. The Gripper: The physical parallel jaw mechanism mounted to Joint 6. [1] (https://www.youtube.com/watch?v=76F6dS4ar8Y)
2. A Solenoid Valve: A 5-way pneumatic solenoid valve (typically 24VDC) to electronically switch the direction of the airflow to open/close the jaws. [1] (https://www.youtube.com/watch?v=76F6dS4ar8Y)
3. Pneumatic Tubing & Fittings: 4mm or 6mm nylon tubing alongside push-to-connect fittings to route air from the compressor regulator to the solenoid valve and down the robot arm. [1] (https://orcaslicer.cn/upload/news/file/20241230/20241230151926_37001.pdf)
4. Relay Board: A relay to let the robot's Arduino controller toggle the 24V power to the solenoid valve. [1] (https://www.youtube.com/watch?v=76F6dS4ar8Y)




## Sources:
- [Hackster: AR4 Mark 5 / AR series history](https://www.hackster.io/news/chris-annin-calls-the-open-source-ar4-robot-arm-done-with-the-new-final-mark-5-revision-62b51e0ae555)
- [ar4_ros_driver](https://github.com/ycheng517/ar4_ros_driver)
- [annin_ar4_driver README](https://docs.ros.org/en/jazzy/p/annin_ar4_driver/__README.html)
- [annin_ar4_driver API](https://docs.ros.org/en/ros2_packages/jazzy/api/annin_ar4_driver/genindex.html)
- [tran-nick/AR3_ROS2](https://github.com/tran-nick/AR3_ROS2)
- [Teensy 4.1 vs 4.0 (DigiKey)](https://forum.digikey.com/t/teensy-4-1-vs-teensy-4-0/22006)
