# Bosch SensorAPI provenance

Unmodified source and licenses downloaded 2026-10-02 from manufacturer repositories:

- BMI08x: https://github.com/boschsensortec/BMI08x_SensorAPI/tree/c1ed227e7bb7da1fa600bbd4e5c82d0da1eb416a
- BMP5: https://github.com/boschsensortec/BMP5_SensorAPI/tree/3bd7c260419e4d08d7fa28343f27e0e6dd276411

Only basic initialization, power/configuration, status and data acquisition APIs are used. BMI088 synchronization firmware and attitude estimation are not enabled. Local Linux callbacks live in src/sensors.cpp. Each directory retains the upstream license.
