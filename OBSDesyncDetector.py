import obspython as obs
import threading
import time
import logging
import json
import psutil
import numpy as np
from datetime import datetime

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("obs_desync_detector.log")]
)
logger = logging.getLogger(__name__)

# Global variables
config = {
    "check_interval": 1.0,
    "alert_thresholds": {
        "dropped_frames_percent": 1.0,
        "render_lag_ms": 15.0,
        "encoding_lag_ms": 20.0,
        "cpu_percent": 80.0,
        "memory_percent": 70.0,
    },
    "enabled": False
}

history = {
    "timestamp": [],
    "dropped_frames": [],
    "total_frames": [],
    "render_time": [],
    "encoding_time": [],
    "cpu_usage": [],
    "memory_usage": [],
}

# Plugin description displayed in OBS
description = "Monitors OBS for lag and desync issues"

# Thread for background monitoring
monitor_thread = None
stop_thread = False
known_issues = set()
last_alert_time = {}

def update_history(stats):
    """Update historical performance data"""
    now = datetime.now()
    
    # Get OBS stats
    output = obs.obs_get_output_by_name("default_stream")
    if output:
        frames_dropped = obs.obs_output_get_frames_dropped(output)
        total_frames = obs.obs_output_get_total_frames(output)
        obs.obs_output_release(output)
    else:
        frames_dropped = 0
        total_frames = 1
    
    # Get process stats
    process = psutil.Process()
    cpu_percent = process.cpu_percent()
    memory_percent = process.memory_percent()
    
    # Add to history
    history["timestamp"].append(now)
    history["dropped_frames"].append(frames_dropped)
    history["total_frames"].append(total_frames)
    history["render_time"].append(stats["render_time"])
    history["encoding_time"].append(stats["encoding_time"])
    history["cpu_usage"].append(cpu_percent)
    history["memory_usage"].append(memory_percent)
    
    # Trim history to 10 minutes
    max_history = int(10 * 60 / config["check_interval"])
    if len(history["timestamp"]) > max_history:
        for key in history:
            history[key] = history[key][-max_history:]

def detect_issues():
    """Analyze history to detect performance issues"""
    if len(history["timestamp"]) < 5:  # Need some data to analyze
        return []
    
    issues = []
    thresholds = config["alert_thresholds"]
    
    # Calculate metrics
    dropped_percent = (history["dropped_frames"][-1] / max(1, history["total_frames"][-1])) * 100
    render_time = history["render_time"][-1]
    encoding_time = history["encoding_time"][-1]
    cpu_percent = history["cpu_usage"][-1]
    memory_percent = history["memory_usage"][-1]
    
    # Check thresholds
    if dropped_percent > thresholds["dropped_frames_percent"]:
        issues.append(f"Frame drops detected: {dropped_percent:.2f}%")
    
    if render_time > thresholds["render_lag_ms"]:
        issues.append(f"High render time: {render_time:.2f}ms")
    
    if encoding_time > thresholds["encoding_lag_ms"]:
        issues.append(f"High encoding time: {encoding_time:.2f}ms")
    
    if cpu_percent > thresholds["cpu_percent"]:
        issues.append(f"High CPU usage: {cpu_percent:.2f}%")
    
    if memory_percent > thresholds["memory_percent"]:
        issues.append(f"High memory usage: {memory_percent:.2f}%")
    
    return issues

def should_alert(issue):
    """Determine if an alert should be sent (avoid alert spam)"""
    now = time.time()
    # Alert once per 5 minutes for the same issue
    if issue in last_alert_time and now - last_alert_time[issue] < 300:
        return False
    
    last_alert_time[issue] = now
    return True

def monitoring_thread_function():
    """Background thread function for monitoring"""
    global stop_thread, known_issues
    
    logger.info("Starting OBS desync detection")
    
    while not stop_thread and config["enabled"]:
        try:
            # Get OBS performance stats
            stats = {
                "render_time": obs.obs_get_average_frame_time_ns() / 1000000,  # Convert ns to ms
                "encoding_time": 0  # We need to estimate this
            }
            
            # Update history
            update_history(stats)
            
            # Detect issues
            issues = detect_issues()
            
            # Log and notify about new issues
            for issue in issues:
                if issue not in known_issues or should_alert(issue):
                    logger.warning(f"ISSUE DETECTED: {issue}")
                    obs.script_log(obs.LOG_WARNING, f"ISSUE DETECTED: {issue}")
                    known_issues.add(issue)
            
            # Remove resolved issues
            current_issue_texts = set(issues)
            resolved_issues = known_issues - current_issue_texts
            for issue in resolved_issues:
                logger.info(f"ISSUE RESOLVED: {issue}")
                obs.script_log(obs.LOG_INFO, f"ISSUE RESOLVED: {issue}")
            known_issues = current_issue_texts
            
            # Normal logging
            if not issues:
                logger.debug("System performing normally")
            
            # Wait for next check
            time.sleep(config["check_interval"])
        
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
            obs.script_log(obs.LOG_ERROR, f"Error in monitoring loop: {e}")
            time.sleep(5)  # Wait before retrying
    
    logger.info("OBS desync detection stopped")

def start_monitoring():
    """Start the monitoring thread"""
    global monitor_thread, stop_thread
    
    if monitor_thread is None or not monitor_thread.is_alive():
        stop_thread = False
        monitor_thread = threading.Thread(target=monitoring_thread_function)
        monitor_thread.daemon = True
        monitor_thread.start()
        obs.script_log(obs.LOG_INFO, "Desync detector started")
    else:
        obs.script_log(obs.LOG_WARNING, "Monitoring already running")

def stop_monitoring():
    """Stop the monitoring thread"""
    global stop_thread, monitor_thread
    
    if monitor_thread is not None and monitor_thread.is_alive():
        stop_thread = True
        monitor_thread.join(timeout=2.0)
        obs.script_log(obs.LOG_INFO, "Desync detector stopped")
    else:
        obs.script_log(obs.LOG_WARNING, "Monitoring not running")

def script_properties():
    """Define properties that the user can change"""
    props = obs.obs_properties_create()
    
    # Add a checkbox to enable/disable the plugin
    obs.obs_properties_add_bool(props, "enabled", "Enable Monitoring")
    
    # Add slider for check interval
    interval_slider = obs.obs_properties_add_float_slider(props, "check_interval", "Check Interval (seconds)", 0.5, 5.0, 0.5)
    
    # Add threshold properties
    obs.obs_properties_add_float(props, "dropped_frames_percent", "Frame Drop Alert Threshold (%)", 0.1, 10.0, 0.1)
    obs.obs_properties_add_float(props, "render_lag_ms", "Render Lag Alert Threshold (ms)", 5.0, 50.0, 1.0)
    obs.obs_properties_add_float(props, "encoding_lag_ms", "Encoding Lag Alert Threshold (ms)", 5.0, 50.0, 1.0)
    obs.obs_properties_add_float(props, "cpu_percent", "CPU Usage Alert Threshold (%)", 50.0, 95.0, 5.0)
    obs.obs_properties_add_float(props, "memory_percent", "Memory Usage Alert Threshold (%)", 50.0, 95.0, 5.0)
    
    # Add a button to generate a report
    obs.obs_properties_add_button(props, "generate_report", "Generate Report", on_generate_report_clicked)
    
    return props

def script_update(settings):
    """Called when the user updates settings"""
    global config
    
    prev_enabled = config["enabled"]
    
    # Update settings
    config["enabled"] = obs.obs_data_get_bool(settings, "enabled")
    config["check_interval"] = obs.obs_data_get_double(settings, "check_interval")
    config["alert_thresholds"]["dropped_frames_percent"] = obs.obs_data_get_double(settings, "dropped_frames_percent")
    config["alert_thresholds"]["render_lag_ms"] = obs.obs_data_get_double(settings, "render_lag_ms")
    config["alert_thresholds"]["encoding_lag_ms"] = obs.obs_data_get_double(settings, "encoding_lag_ms")
    config["alert_thresholds"]["cpu_percent"] = obs.obs_data_get_double(settings, "cpu_percent")
    config["alert_thresholds"]["memory_percent"] = obs.obs_data_get_double(settings, "memory_percent")
    
    # Start or stop monitoring based on enabled state
    if not prev_enabled and config["enabled"]:
        start_monitoring()
    elif prev_enabled and not config["enabled"]:
        stop_monitoring()

def script_defaults(settings):
    """Set default values for settings"""
    obs.obs_data_set_default_bool(settings, "enabled", False)
    obs.obs_data_set_default_double(settings, "check_interval", 1.0)
    obs.obs_data_set_default_double(settings, "dropped_frames_percent", 1.0)
    obs.obs_data_set_default_double(settings, "render_lag_ms", 15.0)
    obs.obs_data_set_default_double(settings, "encoding_lag_ms", 20.0)
    obs.obs_data_set_default_double(settings, "cpu_percent", 80.0)
    obs.obs_data_set_default_double(settings, "memory_percent", 70.0)

def on_generate_report_clicked(props, prop):
    """Handle Generate Report button click"""
    generate_performance_report()
    return True

def generate_performance_report():
    """Generate a report of performance metrics"""
    if len(history["timestamp"]) == 0:
        obs.script_log(obs.LOG_WARNING, "No performance data available yet.")
        return
    
    # Calculate averages
    avg_dropped_percent = sum(d / max(1, t) * 100 for d, t in zip(
        history["dropped_frames"], history["total_frames"])) / len(history["timestamp"])
    
    avg_render = sum(history["render_time"]) / len(history["render_time"])
    avg_encoding = sum(history["encoding_time"]) / len(history["encoding_time"])
    avg_cpu = sum(history["cpu_usage"]) / len(history["cpu_usage"])
    avg_memory = sum(history["memory_usage"]) / len(history["memory_usage"])
    
    # Generate report
    report = [
        "=== OBS Performance Report ===",
        f"Time period: {history['timestamp'][0]} to {history['timestamp'][-1]}",
        f"Samples: {len(history['timestamp'])}",
        "",
        f"Average dropped frames: {avg_dropped_percent:.2f}%",
        f"Average render time: {avg_render:.2f}ms",
        f"Average encoding time: {avg_encoding:.2f}ms",
        f"Average CPU usage: {avg_cpu:.2f}%",
        f"Average memory usage: {avg_memory:.2f}%",
        "",
        "Current issues:",
    ]
    
    if known_issues:
        for issue in known_issues:
            report.append(f"- {issue}")
    else:
        report.append("- None detected")
    
    report_str = "\n".join(report)
    
    # Log the report
    for line in report:
        obs.script_log(obs.LOG_INFO, line)
    
    # Also save to file
    try:
        with open("obs_performance_report.txt", "w") as f:
            f.write(report_str)
        obs.script_log(obs.LOG_INFO, "Report saved to obs_performance_report.txt")
    except Exception as e:
        obs.script_log(obs.LOG_ERROR, f"Failed to save report: {e}")

def script_unload():
    """Called when the script is unloaded"""
    stop_monitoring()