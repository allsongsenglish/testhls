import subprocess
import json
import os
import math # Added for math.ceil

def install_ffmpeg():
    use_sudo = 'COLAB_GPU' in os.environ or os.geteuid() != 0
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, text=True)
        print("✅ FFmpeg is already installed.")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("FFmpeg not found. Attempting to install...")
        try:
            print("Updating package lists...")
            update_cmd = ["apt-get", "update"]
            if use_sudo: update_cmd.insert(0, "sudo")
            subprocess.run(update_cmd, check=True, capture_output=True)

            print("Installing FFmpeg...")
            install_cmd = ["apt-get", "install", "-y", "ffmpeg"]
            if use_sudo: install_cmd.insert(0, "sudo")
            subprocess.run(install_cmd, check=True, capture_output=True)

            print("✅ FFmpeg installed successfully.")
            result = subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, text=True)
            print(result.stdout.split('\n')[0])
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Error installing FFmpeg: {e}")
            stderr = e.stderr.decode() if e.stderr else "No stderr"
            if " আরেকটা apt প্রসেস দ্বারা ব্যবহৃত হচ্ছে" in stderr or "is held by another apt process" in stderr:
                 print("🔒 Another apt process is running...")
            elif "Could not open lock file" in stderr or "লক ফাইল খোলা যায়নি" in stderr:
                print("🔒 Failed to get a lock for package installation...")
            else:
                print(f"Stderr: {stderr}")
            return False
        except FileNotFoundError:
            print("❌ Error installing FFmpeg: apt-get command not found.")
            return False

def get_video_metadata(video_file_path):
    if not os.path.exists(video_file_path):
        print(f"❌ Error: Video file not found at {video_file_path}")
        return {"total_duration_sec": 0, "stream_bitrates_bps": [], "error": f"File not found: {video_file_path}"}

    command = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", video_file_path]
    try:
        print(f"Executing ffprobe for: {video_file_path}")
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        metadata = json.loads(result.stdout)

        total_duration_sec = 0
        if 'format' in metadata and 'duration' in metadata['format']:
            total_duration_sec = float(metadata['format']['duration'])
        else:
            return {"total_duration_sec": 0, "stream_bitrates_bps": [], "error": "Could not determine video duration."}

        stream_bitrates_bps = []
        if 'streams' in metadata:
            for stream in metadata['streams']:
                bit_rate = stream.get('bit_rate', '0')
                if bit_rate == 'N/A': bit_rate = '0'
                try:
                    stream_bitrates_bps.append(int(bit_rate))
                except ValueError:
                    print(f"Warning: Could not parse bit_rate '{stream.get('bit_rate')}' for a stream. Using 0 bps.")
                    stream_bitrates_bps.append(0)

        if not stream_bitrates_bps and total_duration_sec > 0:
            format_bit_rate = metadata.get('format', {}).get('bit_rate')
            if format_bit_rate:
                try:
                    print(f"Warning: No individual stream bitrates. Using overall format bit_rate: {format_bit_rate} bps.")
                    stream_bitrates_bps.append(int(format_bit_rate))
                except ValueError:
                    print(f"Warning: Could not parse format bit_rate '{format_bit_rate}'. Assuming 0 total bps.")
            else:
                 print("Warning: No stream bitrates found and no overall format bitrate. Assuming 0 total bps.")

        if not stream_bitrates_bps: # If still no bitrates (e.g. from format either)
            # This is important because a sum of an empty list is 0, leading to div by zero later
            # if not handled.
             print("Warning: No bitrate information could be extracted from any stream or format level.")
             # We will let calculate_segment_duration handle the zero total bitrate scenario.

        return {"total_duration_sec": total_duration_sec, "stream_bitrates_bps": stream_bitrates_bps, "error": None}

    except subprocess.CalledProcessError as e:
        error_message = f"ffprobe command failed: {e.stderr}"
        if "No such file or directory" in e.stderr: error_message = f"ffprobe error: Video file not found at {video_file_path} or ffprobe itself is missing."
        elif "Invalid data found when processing input" in e.stderr: error_message = f"ffprobe error: Invalid data for {video_file_path}. File might be corrupted/not media."
        print(f"❌ {error_message}")
        return {"total_duration_sec": 0, "stream_bitrates_bps": [], "error": error_message}
    except json.JSONDecodeError:
        error_message = "Failed to parse ffprobe JSON output."
        print(f"❌ {error_message}")
        return {"total_duration_sec": 0, "stream_bitrates_bps": [], "error": error_message}
    except FileNotFoundError:
        error_message = "ffprobe command not found. Ensure FFmpeg is installed and in PATH."
        print(f"❌ {error_message}")
        install_ffmpeg()
        return {"total_duration_sec": 0, "stream_bitrates_bps": [], "error": error_message}

def calculate_segment_duration(video_file_path, target_segment_size_mb=22):
    """
    Calculates estimated segment duration based on video metadata and target segment size.
    """
    metadata = get_video_metadata(video_file_path)

    if metadata.get("error"):
        return {"error": f"Failed to get video metadata: {metadata['error']}"}

    if metadata["total_duration_sec"] <= 0:
        return {"error": "Video duration is zero or invalid. Cannot calculate segment duration."}

    stream_bitrates_bps = metadata["stream_bitrates_bps"]
    if not stream_bitrates_bps: # Handles empty list
        return {"error": "No bitrate data found for the video. Cannot estimate segment size."}

    total_bitrate_bps = sum(stream_bitrates_bps)
    if total_bitrate_bps <= 0:
        return {"error": "Total bitrate of the video is zero or negative. Cannot calculate segment duration. The file might not be a video or audio file, or lacks bitrate information."}

    total_bitrate_mbps = total_bitrate_bps / 1_000_000  # Convert bps to Mbps

    target_segment_size_mbits = target_segment_size_mb * 8  # Convert MB to Mbits

    safe_segment_duration_sec = target_segment_size_mbits / total_bitrate_mbps

    total_video_duration_sec = metadata["total_duration_sec"]

    if safe_segment_duration_sec <= 0: # Should not happen if bitrate and size are positive
        return {"error": "Calculated segment duration is zero or negative. Check bitrate and target size."}

    estimated_total_segments = math.ceil(total_video_duration_sec / safe_segment_duration_sec)

    return {
        "max_bitrate_mbps": total_bitrate_mbps,
        "safe_segment_duration_sec": safe_segment_duration_sec,
        "total_video_duration_sec": total_video_duration_sec,
        "estimated_total_segments": int(estimated_total_segments), # Ensure it's an int for output
        "error": None
    }

if __name__ == "__main__":
    print("🚀 Starting HLS Segment Estimator Script")
    ffmpeg_ready = install_ffmpeg()

    if not ffmpeg_ready:
        print("🔴 FFmpeg installation failed or was skipped. Aborting script.")
    else:
        print("\n--- Initializing Video Path for Estimation ---")
        # User should replace this with the actual path to their large video file in Colab
        # e.g., video_path = "/content/my_very_large_movie.mkv"
        video_path = "test_video.mp4"

        # Try to create a dummy video if the specified one doesn't exist (for testing flow)
        if not os.path.exists(video_path):
            print(f"⚠️ Video file '{video_path}' not found.")
            # Attempt to create a dummy video only if ffmpeg is confirmed ready
            print(f"Attempting to create a dummy video '{video_path}' for demonstration purposes...")
            try:
                # A short, low-quality video to ensure ffprobe gets some data
                subprocess.run([
                    "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100:d=5", # 5s audio
                    "-f", "lavfi", "-i", "testsrc=duration=5:size=320x240:rate=15", # 5s video
                    "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                    "-c:a", "aac", "-b:a", "64k", # Low audio bitrate
                    "-b:v", "250k", # Low video bitrate
                    "-shortest", video_path
                ], capture_output=True, check=True, text=True)
                print(f"✅ Successfully created dummy video: {video_path}")
            except Exception as e:
                print(f"❌ Failed to create dummy video: {e}")
                print("   Please ensure you have a valid video file and update 'video_path'.")
                video_path = "" # Prevent further processing if dummy creation fails

        if video_path: # Proceed if we have a video path (either user-provided or dummy created)
            print(f"\n--- Calculating Segment Duration for: {video_path} ---")
            estimation_results = calculate_segment_duration(video_path, target_segment_size_mb=22)

            if estimation_results and estimation_results.get("error") is None:
                print("\n" + "="*40)
                print("✨ HLS Segmentation Estimation Results ✨")
                print("="*40)
                print(f"🎯 Max Bitrate Detected: {estimation_results['max_bitrate_mbps']:.2f} Mbps")
                print(f"📦 Safe Segment Duration: {estimation_results['safe_segment_duration_sec']:.2f} sec")
                print(f"⏱ Total Video Duration: {estimation_results['total_video_duration_sec']:.2f} sec")
                print(f"📊 Estimated Total Segments: {estimation_results['estimated_total_segments']}")
                print("="*40)
                print("\n⚠️ Note: This is an estimation. Actual segment sizes may vary, especially with VBR content.")
                print("   For precise control, consider using FFmpeg's segmentation options with a safety margin,")
                print("   or analyzing with a more detailed bitrate scanner if peaks are a concern.")
            elif estimation_results:
                print(f"\n❌ Error calculating segment duration: {estimation_results.get('error')}")
            else:
                print("\n❌ Failed to get segment duration estimation (function returned None).")
        else:
            print("\nℹ️ Skipping segment duration calculation as no valid video path was available.")

    print("\nScript finished.")
