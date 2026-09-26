#!/usr/bin/env python3
"""Reproduce the six-trial CM5 encoder comparison as editorial vector figures.

Rendering dependency: matplotlib==3.10.8. No camera, network or target operations.
Input summaries are required to contain both complete control/experiment/control
campaigns. Output must be a new directory, preserving previous evidence exports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = (
    ("encoder-input-comparison.json", "input", (
        ("fullsensor-input-direct-001", "Direct / control 1", "control"),
        ("fullsensor-input-copy-001", "Copy into CPU memory", "copy"),
        ("fullsensor-input-direct-002", "Direct / control 2", "control"))),
    ("allocator-comparison.json", "allocator", (
        ("fullsensor-allocator-control-001", "Default / control 1", "control"),
        ("fullsensor-allocator-cached-001", "Cached capture buffer", "cached"),
        ("fullsensor-allocator-control-002", "Default / control 2", "control"))),
)
PROFILE = {"width": 2064, "height": 1552, "fps": 30, "bitrate": 4_000_000,
           "vbv_bits": 2_000_000, "preset": "ultrafast", "encoder_threads": 2,
           "encode": True, "record": True, "mux_bitrate": 9_000_000,
           "duration_seconds": 90, "segment_seconds": 60}
BG, INK, MUTED, RULE = "#F5F2EA", "#192B2B", "#66736F", "#D8DDD5"
COLORS = {"control": "#66736F", "copy": "#B04B34", "cached": "#137762"}
POSITIONS = (0., 1., 2., 3.8, 4.8, 5.8)


def finite(value, label, *, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{label}: expected a finite number >= {minimum}")
    return value


def counter(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label}: expected a nonnegative integer counter")
    return value


def load_sources(source_dir: Path) -> dict:
    """Validate completed runs and rename misleading legacy rate-field names."""
    trials, sources = [], []
    for filename, campaign, expected in EXPECTED:
        path = source_dir / filename
        data = path.read_bytes()
        rows = json.loads(data)
        if not isinstance(rows, list) or [r.get("name") for r in rows] != [e[0] for e in expected]:
            raise ValueError(f"{filename}: all three ordered trials must be complete before rendering")
        sources.append({"file": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest()})
        for row, (name, label, kind) in zip(rows, expected):
            config = row["configuration"]
            if any(type(config.get(k)) is not type(v) or config[k] != v for k, v in PROFILE.items()):
                raise ValueError(f"{name}: configuration differs from the declared full-sensor comparison profile")
            if not config.get("udp_destination") or sorted(c.get("id") for c in config.get("cameras", [])) != ["A", "B"]:
                raise ValueError(f"{name}: expected simultaneous A/B capture, recording and UDP")
            if row.get("capture_exit") != 0 or row.get("archive_copy_exit") != 0:
                raise ValueError(f"{name}: capture/archive did not finish successfully")
            expected_input = "copy" if kind == "copy" else "dmabuf"
            expected_allocator = "dma_heap_cached" if kind == "cached" else "libcamera"
            if config.get("encoder_input") != expected_input or config.get("capture_allocator", "libcamera") != expected_allocator:
                raise ValueError(f"{name}: input/allocator does not match the labelled experiment")
            seconds = finite(row.get("measured_seconds"), name + ".measured_seconds", minimum=1)
            if not 70 < seconds < 90:
                raise ValueError(f"{name}: unexpected steady-window duration; review the measurement protocol")
            cpu = finite(row.get("cpu_mean_percent"), name + ".cpu_mean_percent")
            if cpu > 100:
                raise ValueError(f"{name}: total CM5 CPU percent must be within 0..100")
            cameras = {}
            for camera_id in ("A", "B"):
                camera = row["cameras"][camera_id]
                timing = camera["timing_mean_us"]
                cameras[camera_id] = {
                    "captured_fps": finite(camera.get("captured_frames"), name + ".captured_fps"),
                    "encoded_fps": finite(camera.get("encoded_frames"), name + ".encoded_fps"),
                    "added_frame_drops": counter(camera.get("additional_drops"), name + ".additional_drops"),
                    "input_and_send_ms": finite(timing.get("encoder_input_and_send"), name + ".service_us") / 1000,
                    "queue_dwell_ms": finite(timing.get("capture_queue_dwell"), name + ".dwell_us") / 1000,
                    "timing_mean_us": timing,
                }
            trials.append({"name": name, "campaign": campaign, "label": label, "kind": kind,
                           "encoder_input": expected_input, "capture_allocator": expected_allocator,
                           "measured_seconds": seconds, "cpu_mean_percent": cpu,
                           "temperature_max_c": finite(row.get("temperature_max_c"), name + ".temperature_c"),
                           "throttled_values": row["throttled_values"], "cameras": cameras,
                           "transport_added_packet_drops": counter(row.get("transport_additional_drops"), name + ".transport_drops")})
    controls = [t for t in trials if t["campaign"] == "allocator" and t["kind"] == "control"]
    cached = next(t for t in trials if t["kind"] == "cached")
    control_service = sum(t["cameras"][c]["input_and_send_ms"] for t in controls for c in "AB") / 4
    cached_service = sum(cached["cameras"][c]["input_and_send_ms"] for c in "AB") / 2
    control_cpu = sum(t["cpu_mean_percent"] for t in controls) / 2
    return {"schema_version": 1, "kind": "measured_encoder_optimization_export", "sources": sources,
            "profile": PROFILE,
            "bench_context": {"source": "experiment controller notes", "scene": "Live bench scene across sequential trials; not replay of identical raw frames",
                              "cooling": "External fan on", "nominal_cpu_clock_ghz": 2.4,
                              "receiver_path": "CM5 Ethernet to LAN; Mac receiver on Wi-Fi",
                              "build_commits": {"input_baseline": "83bef5e", "baseline_header_fix": "b45771d", "cached_allocator": "4934186"}},
            "warmup_exclusion_seconds": 10, "frame_cadence_ms": 1000 / PROFILE["fps"],
            "qualification_status": "short_trials_not_one_hour_qualification", "trials": trials,
            "descriptive_comparison": {
                "reference": "Unweighted mean of A/B mean service times in the two allocator control trials",
                "control_input_and_send_ms": control_service, "cached_input_and_send_ms": cached_service,
                "input_and_send_reduction_percent": 100 * (1 - cached_service / control_service),
                "control_cpu_mean_percent": control_cpu, "cached_cpu_mean_percent": cached["cpu_mean_percent"],
                "cpu_reduction_percentage_points": control_cpu - cached["cpu_mean_percent"]},
            "definitions": {
                "encoded_fps": "Difference of native encoded-frame counters divided by the health-window duration; not browser-delivered or displayed fps.",
                "input_and_send_ms": "Mean encoder_input_and_send wall-clock samples: input copy when enabled plus direct FFmpeg send call(s); excludes DMA sync/release, receive calls, queues and output work.",
                "queue_dwell_ms": "Mean enqueue-to-worker-dequeue duration of accepted frames; rejected frames have no dwell sample.",
                "cpu_mean_percent": "Arithmetic mean of health cpu_busy_percent samples from aggregate /proc/stat; 100% is all CM5 cores busy, not 100% per core.",
                "added_frame_drops": "Difference of cumulative camera dropped_frames counters within the post-warmup window; startup drops are outside this window.",
                "measurement_window": "Active dual-camera health samples with pts_us >= 10,000,000; rates/timing/counter deltas use first and last selected samples. CPU/temperature use all selected samples.",
                "uncertainty": "Each plotted control is one observed sequential live-scene run, not a replay of identical raw frames. No statistical significance, image-quality comparison, uncertainty estimates, or one-hour qualification are inferred."}}



def load_followup(path: Path) -> dict:
    """Keep later preset/extended evidence separate from the six plotted trials."""
    data = path.read_bytes()
    rows = json.loads(data)
    expected = [("superfast", 90), ("veryfast", 90), ("ultrafast", 180)]
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("Subsequent follow-up must contain all three completed preset/extended trials")
    trials = []
    for row, (preset, duration) in zip(rows, expected):
        config = row["configuration"]
        wanted = dict(PROFILE, preset=preset, duration_seconds=duration)
        if any(type(config.get(k)) is not type(v) or config[k] != v for k, v in wanted.items()):
            raise ValueError("Follow-up profile differs from the documented cached-buffer preset comparison")
        if (config.get("capture_allocator") != "dma_heap_cached" or config.get("encoder_input") != "dmabuf"
                or not config.get("udp_destination") or row.get("capture_exit") != 0 or row.get("archive_copy_exit") != 0):
            raise ValueError("Follow-up requires completed cached-buffer direct-input recording/UDP trials")
        trials.append({"name": row["name"], "preset": preset, "configured_seconds": duration,
                       "measured_seconds": finite(row["measured_seconds"], "followup duration", minimum=1),
                       "cpu_mean_percent": finite(row["cpu_mean_percent"], "followup CPU"),
                       "cameras": {camera: {
                           "encoded_fps": finite(row["cameras"][camera]["encoded_frames"], "followup fps"),
                           "added_frame_drops": counter(row["cameras"][camera]["additional_drops"], "followup drops")}
                           for camera in "AB"}})
    return {"included_in_figures_or_primary_comparison": False,
            "source": {"file": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest()},
            "trials": trials,
            "interpretation": ["The longer ultrafast trial had additional camera drops; the earlier zero-drop window does not establish sustained drop-free operation.",
                               "Superfast and veryfast missed the nominal 30 fps target in these trials.",
                               "These remain sequential live-scene bench trials, not one-hour qualification or image-quality comparisons."]}


def configure_plotting():
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
        "text.color": INK, "axes.labelcolor": MUTED, "xtick.color": MUTED,
        "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
        "svg.fonttype": "path", "svg.hashsalt": "pigeonvision-encoder-001", "pdf.fonttype": 42})
    import matplotlib.pyplot as plt
    return plt


def base_figure(plt, page, title, subtitle, highlight, highlight_note):
    from matplotlib.lines import Line2D
    fig = plt.figure(figsize=(16, 10.5))
    fig.text(.052, .954, "PIGEONVISION  /  CM5 ENGINEERING NOTES", fontsize=10, weight="bold", color=MUTED)
    fig.text(.95, .954, f"001    /    {page:02d}", ha="right", fontsize=10, color=MUTED)
    fig.add_artist(Line2D([.052, .95], [.934, .934], transform=fig.transFigure, color=INK, lw=1))
    fig.text(.052, .864, title, fontsize=31, weight="bold", va="baseline")
    fig.text(.052, .818, subtitle, fontsize=12.5, color=MUTED)
    fig.text(.952, .863, highlight, ha="right", fontsize=35, weight="bold", color=COLORS["cached"])
    fig.text(.952, .823, highlight_note, ha="right", fontsize=10.5, color=MUTED)
    fig.text(.052, .771, "2064 × 1552  ·  TWO CAMERAS  ·  30 FPS  ·  4 Mb/s EACH  ·  x264 ULTRAFAST  ·  2 THREADS EACH", fontsize=10, weight="bold")
    fig.text(.052, .744, "Simultaneous Matroska recording + 9 Mb/s MPEG-TS over UDP. Six sequential 90-second bench trials.", fontsize=10.5, color=MUTED)
    fig.text(.052, .717, "Live scene, not identical-frame replay  ·  External fan  ·  Nominal 2.4 GHz  ·  Mac receiver via LAN / Wi-Fi", fontsize=9.5, color=MUTED)
    return fig


def axis(fig, rect, title, ticks, limit, *, unit):
    ax = fig.add_axes(rect)
    ax.set_xlim(0, limit)
    ax.set_ylim(6.36, -.72)
    ax.set_yticks([])
    ax.set_xticks(ticks)
    ax.tick_params(axis="x", length=0, pad=8, labelsize=10)
    for edge in ax.spines:
        ax.spines[edge].set_visible(False)
    ax.grid(axis="x", color=RULE, lw=.7, zorder=0)
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold", pad=20)
    ax.set_xlabel(unit, fontsize=10, labelpad=12, loc="left")
    ax.axhline(2.88, color=RULE, lw=.9)
    return ax


def row_labels(fig, ax, trials):
    for y, trial in zip(POSITIONS, trials):
        fy = fig.transFigure.inverted().transform(ax.transData.transform((0, y)))[1]
        fig.text(.052, fy+.006, trial["label"], fontsize=11, weight="bold", color=COLORS[trial["kind"]])
        suffix = " · NOT RETAINED" if trial["kind"] == "copy" else ""
        fig.text(.052, fy-.015, f'{trial["measured_seconds"]:.2f} s measured{suffix}', fontsize=8.5, color=MUTED)
    for y, title in ((-.6, "01  /  ENCODER INPUT"), (3.2, "02  /  CAPTURE ALLOCATOR")):
        fy = fig.transFigure.inverted().transform(ax.transData.transform((0, y)))[1]
        fig.text(.052, fy, title, fontsize=9, weight="bold", color=MUTED)


def paired_bars(ax, trials, metric, fmt, offset):
    from matplotlib.colors import to_rgb
    for y, trial in zip(POSITIONS, trials):
        color = COLORS[trial["kind"]]
        light = tuple(.76 + .24*v for v in to_rgb(color))
        for camera, dy, face in (("A", -.145, color), ("B", .145, light)):
            value = trial["cameras"][camera][metric]
            ax.barh(y+dy, value, height=.21, facecolor=face, edgecolor=color, linewidth=.85, zorder=3)
            if value == 0:
                ax.plot([0], [y+dy], marker="|", color=color, markersize=9, zorder=4)
            ax.text(value+offset, y+dy, fmt(value), ha="left", va="center", fontsize=10,
                    fontfamily="DejaVu Sans Mono", color=INK, bbox={"facecolor": BG, "edgecolor": "none", "pad": .5})


def legend(fig, x=.052, y=.205):
    from matplotlib.patches import Rectangle
    fig.add_artist(Rectangle((x, y), .012, .011, transform=fig.transFigure, fc=INK, ec=INK))
    fig.text(x+.018, y+.001, "Camera A", fontsize=10)
    fig.add_artist(Rectangle((x+.10, y), .012, .011, transform=fig.transFigure, fc=BG, ec=INK))
    fig.text(x+.118, y+.001, "Camera B", fontsize=10)
    fig.text(.952, y+.001, "One mark = one measured run; no statistical error bars.", ha="right", fontsize=9.5, color=MUTED)


def footer(fig, line1, line2):
    from matplotlib.lines import Line2D
    fig.add_artist(Line2D([.052, .95], [.108, .108], transform=fig.transFigure, color=RULE, lw=1))
    fig.text(.052, .082, line1, fontsize=9, color=MUTED)
    fig.text(.052, .058, line2, fontsize=9, color=MUTED)
    fig.text(.952, .028, "MEASURED BENCH RESULTS  /  NOT ONE-HOUR QUALIFICATION", ha="right", fontsize=8, weight="bold", color=MUTED)


def performance_figure(plt, summary):
    comparison, trials = summary["descriptive_comparison"], summary["trials"]
    fig = base_figure(plt, 1, "A faster path into x264.",
        "Mean input/send time fell below the 33.33 ms cadence in the cached-buffer trial.",
        f'{comparison["input_and_send_reduction_percent"]:.1f}%', "less mean input + send time¹")
    service = axis(fig, [.29, .27, .375, .40], "Input + encoder send", [0, 10, 20, 30, 40], 45.5, unit="MEAN WALL-CLOCK SERVICE TIME / ms")
    fps = axis(fig, [.765, .27, .178, .40], "Encoded output", [0, 10, 20, 30], 34, unit="ENCODED FRAMES / SECOND")
    paired_bars(service, trials, "input_and_send_ms", lambda x: f"{x:.2f}", .55)
    paired_bars(fps, trials, "encoded_fps", lambda x: f"{x:.2f}", .5)
    service.axvline(summary["frame_cadence_ms"], color=INK, ls=(0,(3,3)), lw=1, zorder=2)
    service.text(summary["frame_cadence_ms"], -.79, "33.33 ms cadence", fontsize=9, ha="center", color=INK)
    fps.axvline(30, color=INK, ls=(0,(3,3)), lw=1, zorder=2)
    row_labels(fig, service, trials)
    legend(fig)
    fig.text(.052, .157, "The copy experiment reduced the send call, but added a separate copy cost. Cached DMA buffers avoided that extra copy.", fontsize=11, weight="bold")
    footer(fig, "¹ Descriptive comparison against the two bracketing allocator controls; A/B means weighted equally. Each control remains visible above.",
           "10 s warmup excluded; ~80 s measured per run. Service time excludes queueing, DMA synchronization, receive calls and output work; it is not glass-to-glass latency.")
    return fig


def systems_figure(plt, summary):
    trials, comparison = summary["trials"], summary["descriptive_comparison"]
    fig = base_figure(plt, 2, "Less work. Less waiting.",
        "The cached-buffer trial measured less backlog and zero added drops after warmup.",
        f'{comparison["cached_cpu_mean_percent"]:.1f}%', "mean CPU across the entire CM5")
    cpu = axis(fig, [.29, .27, .18, .40], "Total CPU", [0, 40, 80], 100, unit="ALL CORES COMBINED / %")
    queue = axis(fig, [.54, .27, .20, .40], "Capture queue dwell", [0, 40, 80, 120], 132, unit="MEAN WAIT BEFORE ENCODING / ms")
    drops = axis(fig, [.815, .27, .129, .40], "Added frame drops", [0, 200, 400], 460, unit="A / B COUNTER INCREASE")
    for y, trial in zip(POSITIONS, trials):
        value = trial["cpu_mean_percent"]
        cpu.barh(y, value, height=.39, color=COLORS[trial["kind"]], zorder=3)
        cpu.text(value+2, y, f"{value:.1f}", va="center", fontsize=10, fontfamily="DejaVu Sans Mono", bbox={"facecolor": BG, "edgecolor": "none", "pad": .5})
    cpu.axvline(80, color=INK, ls=(0,(3,3)), lw=1, zorder=2)
    cpu.text(80, -.79, "80% target", fontsize=9, ha="center")
    paired_bars(queue, trials, "queue_dwell_ms", lambda x: f"{x:.2f}", 2)
    paired_bars(drops, trials, "added_frame_drops", lambda x: str(x), 8)
    row_labels(fig, cpu, trials)
    legend(fig)
    transport = sum(t["transport_added_packet_drops"] for t in trials)
    throttle = sorted({v for t in trials for v in t["throttled_values"]})
    max_temp = max(t["temperature_max_c"] for t in trials)
    fig.text(.052, .158, f"{transport} added transport packet drops across these windows.  Maximum temperature: {max_temp:.1f} °C.  Throttle flags: {throttle}.", fontsize=11, weight="bold")
    footer(fig, "CPU uses aggregate /proc/stat: 100% means all cores busy. Queue means cover accepted frames only. Dropped-frame counts exclude the warmup period.",
           "Short trials, one cached-allocator run, sequential controls. Zero added drops here does not mean zero startup drops, verified exposure synchronization, or flight qualification.")
    return fig


def evidence_readme(summary):
    lines = ["# PigeonVision — encoder optimization evidence", "", "Two portfolio figures from six actual CM5 trials. These are short comparative trials, **not one-hour qualification**.", "",
             "- `01-encoder-path.{png,svg,pdf}`: input/send service time and encoded output rate.",
             "- `02-system-response.{png,svg,pdf}`: total CPU, queue dwell and added frame drops.",
             "- `encoder-optimization.pdf`: both figures as vector PDF pages.",
             "- `source-summary.json`: exact plotted values, units, definitions, source SHA-256 hashes and descriptive comparison arithmetic.",
             "- `plot-requirements.txt`: exact installed plotting environment versions.", "",
             "## Conditions and measurement", "",
             "Dual colour IMX900 on the CM5; 2064×1552 at nominal 30 fps per camera; x264 ultrafast, 2 threads per encoder; 4 Mb/s and 2 Mbit VBV each. Both encodes feed segmented Matroska recording and a 9 Mb/s MPEG-TS UDP stream. Each run was configured for 90 seconds. The first 10 seconds were excluded; actual active dual-camera measurement windows are listed below.", "",
             "| Trial | Window (s) | Input/send A / B (ms) | Encoded A / B (fps) | CPU (%) | Dwell A / B (ms) | Added drops A / B |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for t in summary["trials"]:
        a, b = t["cameras"]["A"], t["cameras"]["B"]
        lines.append(f'| {t["name"]} | {t["measured_seconds"]:.6f} | {a["input_and_send_ms"]:.3f} / {b["input_and_send_ms"]:.3f} | {a["encoded_fps"]:.3f} / {b["encoded_fps"]:.3f} | {t["cpu_mean_percent"]:.3f} | {a["queue_dwell_ms"]:.3f} / {b["queue_dwell_ms"]:.3f} | {a["added_frame_drops"]} / {b["added_frame_drops"]} |')
    lines += ["", "## Interpretation limits", ""]
    for key, value in summary["definitions"].items():
        lines.append(f"- **{key}**: {value}")
    lines += ["- The 33.33 ms line is the nominal frame interval, not a latency limit or a proof that all individual frames meet a deadline.",
              "- Copy was not retained: a shorter send call was outweighed by the additional input copy. The plots use the measured combined input-and-send value so this cost is not hidden.",
              "- Live bench scene across sequential runs, not replay of identical raw frames. Controls bracket the changes; no statistical significance or image-quality comparison is claimed. External fan on, nominal 2.4 GHz. The CM5 streams over Ethernet to the LAN; the Mac receiver uses Wi-Fi.",
              "- Build provenance from experiment-controller notes: baseline `83bef5e`, header correction `b45771d`, cached-allocator import `4934186`. These are source commits, not a promise of byte-identical binary builds.",
              "- Allocator control builds include extra DMA timing instrumentation absent from the earlier input campaign. Compare interventions within their bracketing campaign; no pooled cross-build uncertainty is asserted.",
              "- Startup camera errors, full-session archive verification, end-to-end network receipt and browser frame delivery are outside these plotted steady-window summaries. Zero added drops does not imply zero startup drops.",
              "- The cached allocator remains an opt-in experiment. These figures do not change software defaults or establish one-hour, synchronization, or flight qualification.", "",
              "## Reproduce", "", "From the repository root with Python 3.11+ and Matplotlib installed (tested: 3.10.8):", "", "```sh", "python software/tools/plot_encoder_optimization.py \\", "  --source-dir output/provisioning \\", "  --output output/portfolio/encoder-optimization-new", "```", "", "Choose a new output directory; existing exports are never overwritten. The tool refuses partial campaigns and mismatched profiles. Input health summaries were produced by `output/provisioning/compare_encoder_inputs.py` and `compare_allocators.py`; rate and timing values are differences of cumulative counters over the selected health window."]
    followup = summary.get("subsequent_followup")
    if followup:
        lines += ["", "## Subsequent follow-up — separate from the figures", "",
                  "Later cached-buffer trials are recorded here without changing or pooling the six plotted runs. The extended ultrafast trial had additional frame drops: the earlier zero-drop ~80-second window must not be read as sustained drop-free operation.", "",
                  "| Preset | Configured / measured seconds | Encoded A / B (fps) | CPU (%) | Added drops A / B |",
                  "| --- | ---: | ---: | ---: | ---: |"]
        for trial in followup["trials"]:
            a, b = trial["cameras"]["A"], trial["cameras"]["B"]
            lines.append(f'| {trial["preset"]} | {trial["configured_seconds"]} / {trial["measured_seconds"]:.3f} | {a["encoded_fps"]:.3f} / {b["encoded_fps"]:.3f} | {trial["cpu_mean_percent"]:.2f} | {a["added_frame_drops"]} / {b["added_frame_drops"]} |')
        lines += ["", *[f"- {note}" for note in followup["interpretation"]],
                  "", "Reproduce this separate appendix by adding `--followup output/provisioning/cached-preset-comparison.json` to the command above. Its source hash and exact values are retained under `subsequent_followup` in `source-summary.json`."]
    return "\n".join(lines) + "\n"


def export(source_dir: Path, output: Path, followup: Path | None = None):
    summary = load_sources(source_dir)
    if followup is not None:
        summary["subsequent_followup"] = load_followup(followup)
    output.mkdir(parents=True, exist_ok=False)
    plt = configure_plotting()
    from matplotlib.backends.backend_pdf import PdfPages
    import importlib.metadata
    summary["rendering"] = {"python": sys.version, "matplotlib": importlib.metadata.version("matplotlib"),
                            "font": "DejaVu Sans / DejaVu Sans Mono", "png_dpi": 300}
    figures = [("01-encoder-path", performance_figure(plt, summary)), ("02-system-response", systems_figure(plt, summary))]
    metadata = {"Title": "PigeonVision — measured encoder optimization", "Author": "PigeonVision", "CreationDate": None, "ModDate": None}
    with PdfPages(output / "encoder-optimization.pdf", metadata=metadata) as pdf:
        for name, fig in figures:
            fig.savefig(output / f"{name}.png", dpi=300)
            fig.savefig(output / f"{name}.svg", metadata={"Date": None})
            fig.savefig(output / f"{name}.pdf", metadata=metadata)
            pdf.savefig(fig)
            plt.close(fig)
    (output / "source-summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    (output / "README.md").write_text(evidence_readme(summary))
    packages = sorted((dist.metadata["Name"], dist.version) for dist in importlib.metadata.distributions())
    (output / "plot-requirements.txt").write_text("\n".join(f"{name}=={version}" for name, version in packages) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=ROOT / "output/provisioning")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--followup", type=Path, help="Separate subsequent preset/extended-trial evidence; does not alter plots")
    args = parser.parse_args(argv)
    try:
        export(args.source_dir, args.output, args.followup)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, f"encoder plot: {exc}\n")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
