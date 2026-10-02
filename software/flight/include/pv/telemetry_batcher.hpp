#pragma once
#include <atomic>
#include <cmath>
#include <map>
#include <mutex>
#include <vector>

#include "pv/config.hpp"
namespace pv {
class TelemetryBatcher {
public:
  void add(const Json &sample) {
    std::lock_guard lock(mutex_);
    if (pending_.size() >= 512) {
      ++dropped_;
      return;
    }
    pending_.push_back(sample);
  }
  std::vector<Json> take(const std::string &session, std::int64_t pts) {
    std::vector<Json> samples;
    {
      std::lock_guard lock(mutex_);
      samples.swap(pending_);
    }
    std::vector<Json> result;
    Json batch = empty(session, pts);
    for (const auto &sample : samples) {
      const auto source = sample.at("source").get<std::string>();
      Json row = Json::array();
      const auto base = sample.value("monotonic_us", std::int64_t(0));
      auto fields = columns();
      Json shared = Json::object();
      if (sample["values"].is_object())
        for (const auto &[key, value] : sample["values"].items()) {
          if (value.is_boolean() || value.is_string())
            shared[key] = value;
          else
            fields.push_back("values." + key);
        }
      for (const auto &column : columns())
        row.push_back(sample.value(column, Json(nullptr)));
      if (sample["values"].is_object())
        for (const auto &[key, value] : sample["values"].items())
          if (!value.is_boolean() && !value.is_string())
            row.push_back(compact(value));
      auto candidate = batch;
      const auto backend = sample.value("backend", std::string("unknown"));
      const auto basis = sample.value("timestamp_basis", std::string("unknown"));
      const auto missed = sample.value("missed_polls", std::uint64_t(0));
      if (candidate["sources"].empty()) {
        candidate["backend"] = backend;
        candidate["timestamp_basis"] = basis;
      }
      auto &groups = candidate["sources"];
      std::size_t index = 0;
      while (index < groups.size() &&
             (groups[index]["source"] != source || groups[index]["columns"] != fields ||
              groups[index]["valid"] != sample.value("valid", false) ||
              groups[index]["status"] != sample.value("status", std::string()) ||
              groups[index]["errors"] != sample.value("errors", 0) ||
              groups[index]["backend"] != backend || groups[index]["timestamp_basis"] != basis ||
              groups[index]["missed_polls"] != missed || groups[index]["shared_values"] != shared))
        ++index;
      if (index == groups.size())
        groups.push_back({{"source", source},
                          {"columns", fields},
                          {"valid", sample.value("valid", false)},
                          {"status", sample.value("status", std::string())},
                          {"errors", sample.value("errors", 0)},
                          {"shared_values", shared},
                          {"backend", backend},
                          {"timestamp_basis", basis},
                          {"missed_polls", missed},
                          {"base_monotonic_us", base},
                          {"rows", Json::array()}});
      for (const auto position : {1, 2, 3})
        if (row[position].is_number_integer())
          row[position] = row[position].get<std::int64_t>() -
                          groups[index]["base_monotonic_us"].get<std::int64_t>();
      groups[index]["rows"].push_back(row);
      candidate["sample_count"] = candidate["sample_count"].get<unsigned>() + 1;
      if (candidate.dump().size() > 2500 && !batch["sources"].empty()) {
        result.push_back(std::move(batch));
        batch = empty(session, pts);
        batch["backend"] = backend;
        batch["timestamp_basis"] = basis;
        batch["sources"].push_back(groups[index]);
        batch["sources"][0]["rows"] = Json::array({row});
        batch["sample_count"] = 1;
      } else
        batch = std::move(candidate);
    }
    if (!batch["sources"].empty())
      result.push_back(std::move(batch));
    for (auto &record : result)
      for (auto &source : record["sources"]) {
        auto expected = columns();
        if (source["valid"].get<bool>())
          for (const auto &key : value_columns(source["source"].get<std::string>()))
            expected.push_back("values." + key);
        if (source["columns"] == expected)
          source.erase("columns");
        if (source["status"] == "fresh")
          source.erase("status");
        if (source["errors"] == 0)
          source.erase("errors");
        if (source["shared_values"].empty())
          source.erase("shared_values");
        if (source["missed_polls"] == 0)
          source.erase("missed_polls");
        if (source["backend"] == record["backend"])
          source.erase("backend");
        if (source["timestamp_basis"] == record["timestamp_basis"])
          source.erase("timestamp_basis");
      }
    return result;
  }

private:
  static Json compact(Json value) {
    if (value.is_number_float()) {
      const auto n = value.get<double>();
      if (n != 0 && std::isfinite(n)) {
        const auto scale = std::pow(10.0, 5 - std::floor(std::log10(std::abs(n))));
        value = std::round(n * scale) / scale;
      }
    } else if (value.is_array())
      for (auto &item : value)
        item = compact(item);
    return value;
  }
  static std::vector<std::string> value_columns(const std::string &source) {
    if (source == "bmi088_accel")
      return {"accel_m_s2"};
    if (source == "bmi088_gyro")
      return {"gyro_rad_s"};
    if (source == "bmp581")
      return {"pressure_pa", "temperature_c"};
    if (source == "ina226")
      return {"bus_voltage_v", "current_a", "power_w", "shunt_voltage_v"};
    return {};
  }
  static std::vector<std::string> columns() {
    return {"sequence", "monotonic_us", "read_start_monotonic_us", "last_good_monotonic_us"};
  }
  Json empty(const std::string &session, std::int64_t pts) const {
    return {{"schema_version", 1},
            {"sample_count", 0},
            {"type", "sensors"},
            {"session_id", session},
            {"pts_us", pts},
            {"clock_domain", "CLOCK_BOOTTIME"},
            {"acquisition_clock_domain", "CLOCK_BOOTTIME"},
            {"value_significant_digits", 6},
            {"dropped_samples", dropped_.load()},
            {"sources", Json::array()}};
  }
  std::mutex mutex_;
  std::vector<Json> pending_;
  std::atomic<std::uint64_t> dropped_{0};
};
} // namespace pv
