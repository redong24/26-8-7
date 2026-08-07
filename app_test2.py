import base64
import sys
import os
from charset_normalizer import detect
import cv2
import time
import numpy as np
import torch
import collections
from mtcnn import MTCNN
import argparse
import threading

from flask import Flask, json, jsonify, render_template, Response, request
from PIL import Image, ImageDraw, ImageFont
from scipy.signal import savgol_filter  # <--- 导入 Savitzky-Golay 滤波器


try:
    from PhysNetModel import PhysNet
    from utils import pulse_rate_from_power_spectral_density
except ImportError as e:
    print(f"导入自定义模块失败: {e}")
    print("请确保 PhysNetModel.py 和 utils.py 文件在正确的路径下。")
    sys.exit(1)

os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'


app = Flask(__name__)


output_frame_webcam = None
output_frame_rppg = None
output_frame_hr_trend = None
g_current_hr_display_val = "0.00 BPM"
lock = threading.Lock()


SEQUENCE_LENGTH = 64
CAMERA_FPS = 30
FACE_TARGET_SIZE = (128, 128)

RPPG_DISPLAY_SECONDS = 10
RPPG_PLOT_BUFFER_MAX_LEN = RPPG_DISPLAY_SECONDS * CAMERA_FPS
PLOT_WIDTH_RPPG = 400
PLOT_HEIGHT_RPPG = 200

HR_CALC_WINDOW_SECONDS = 2
HR_CALC_WINDOW_RPPG_LEN = HR_CALC_WINDOW_SECONDS * CAMERA_FPS
HR_UPDATE_INTERVAL_SECONDS = 1

HR_PLOT_HISTORY_LEN = 60
HR_PLOT_WIDTH = 400
HR_PLOT_HEIGHT = 200
HR_PLOT_Y_MIN = 40
HR_PLOT_Y_MAX = 180
HR_SMOOTHING_WINDOW_SIZE = 5
HR_SMOOTHING_POLYORDER = 2

PLOT_BG_COLOR = (240, 240, 240)
RPPG_WAVE_COLOR = (0, 128, 0)
HR_LINE_COLOR = (220, 20, 60)
AXIS_COLOR = (80, 80, 80)
TEXT_COLOR_DARK = (50, 50, 50)
TEXT_COLOR_LIGHT = (100, 100, 100)
TICK_LENGTH = 4
LABEL_FONT_THICKNESS = 1
LABEL_FONT_SCALE_CV2 = 0.4
AXIS_LABEL_FONT_SCALE_CV2 = 0.45
HR_TEXT_FONT_SCALE_ON_GRAPH_CV2 = 0.7
HR_TEXT_FONT_THICKNESS_ON_GRAPH_CV2 = 2

FONT_SIZE_AXIS_LABEL_PIL = 15
FONT_SIZE_TICK_LABEL_PIL = 12
FONT_SIZE_HR_ON_GRAPH_PIL = 18

AXIS_LABEL_FONT_PIL = None
TICK_LABEL_FONT_PIL = None
HR_FONT_PIL = None

g_args = None


def draw_text_pil_safe(image_np, text, position, font_pil, color_bgr, fallback_font_details=None):
    if font_pil:
        try:
            image_pil = Image.fromarray(cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(image_pil)
            color_rgb = (color_bgr[2], color_bgr[1], color_bgr[0])
            draw.text(position, text, font=font_pil, fill=color_rgb)
            return cv2.cvtColor(np.array(image_pil), cv2.COLOR_RGB2BGR)
        except Exception as e:
            print(f"Pillow绘制文本 '{text}' 时出错: {e}")
            if fallback_font_details:
                font_face, font_scale, thickness = fallback_font_details
                (text_w, text_h), baseline = cv2.getTextSize(text, font_face, font_scale, thickness)
                adjusted_y_position = (position[0], position[1] + text_h)
                cv2.putText(image_np, text, adjusted_y_position, font_face, font_scale, color_bgr, thickness,
                            cv2.LINE_AA)
            return image_np
    elif fallback_font_details:
        font_face, font_scale, thickness = fallback_font_details
        (text_w, text_h), baseline = cv2.getTextSize(text, font_face, font_scale, thickness)
        adjusted_y_position = (position[0], position[1] + text_h)
        cv2.putText(image_np, text, adjusted_y_position, font_face, font_scale, color_bgr, thickness, cv2.LINE_AA)
        return image_np
    return image_np


def rppg_processing_loop(args_obj):
    global output_frame_webcam, output_frame_rppg, output_frame_hr_trend, lock, g_current_hr_display_val
    global AXIS_LABEL_FONT_PIL, TICK_LABEL_FONT_PIL, HR_FONT_PIL

    device = args_obj.device
    print(f"后台线程使用设备: {device}")
    detector = MTCNN()
    model = PhysNet(S=2, in_ch=3)
    try:
        model.load_state_dict(torch.load(args_obj.model_path, map_location=device))
        print(f"后台线程: 模型已从 {args_obj.model_path} 加载。")
    except Exception as e:
        print(f"后台线程加载模型时出错: {e}")
        # cap.release()
        return
    model = model.to(device)
    model.eval()

    frame_buffer_for_model = collections.deque(maxlen=SEQUENCE_LENGTH)
    rppg_plot_buffer = collections.deque(maxlen=RPPG_PLOT_BUFFER_MAX_LEN)
    hr_history_buffer = collections.deque(maxlen=HR_PLOT_HISTORY_LEN)
    current_hr_numeric = 0.0
    last_hr_update_time = time.time()

    plot_margin_left = 45
    plot_margin_bottom = 35
    plot_margin_top = 25
    plot_margin_right = 15

    cv2_axis_label_font_details = (cv2.FONT_HERSHEY_SIMPLEX, AXIS_LABEL_FONT_SCALE_CV2, LABEL_FONT_THICKNESS)
    cv2_tick_label_font_details = (cv2.FONT_HERSHEY_SIMPLEX, LABEL_FONT_SCALE_CV2 - 0.05, LABEL_FONT_THICKNESS)
    cv2_hr_on_graph_font_details = (
    cv2.FONT_HERSHEY_SIMPLEX, HR_TEXT_FONT_SCALE_ON_GRAPH_CV2, HR_TEXT_FONT_THICKNESS_ON_GRAPH_CV2)

    while True:
        # 从全局变量获取最新帧
        with lock:
            if output_frame_webcam is None:
                time.sleep(0.01)
                continue
            frame = output_frame_webcam.copy()

        display_frame_np = frame.copy()
        # ... (人脸检测和rPPG模型推理逻辑与之前版本相同) ...
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        faces = detector.detect_faces(frame_rgb)
        face_roi_processed_tensor = None

        if faces:
            face_data = faces[0]
            x, y, w, h = face_data['box']
            x, y = max(0, x), max(0, y)
            face_roi_bgr = frame[y:min(y + h, frame.shape[0] - 1), x:min(x + w, frame.shape[1] - 1)]
            if face_roi_bgr.size > 0:
                cv2.rectangle(display_frame_np, (x, y), (x + w, y + h), (0, 255, 0), 2)
                face_roi_resized = cv2.resize(face_roi_bgr, FACE_TARGET_SIZE, interpolation=cv2.INTER_AREA)
                face_roi_normalized = face_roi_resized / 255.0
                face_tensor_chw = torch.from_numpy(face_roi_normalized).permute(2, 0, 1).float()
                face_roi_processed_tensor = face_tensor_chw

        if face_roi_processed_tensor is not None:
            frame_buffer_for_model.append(face_roi_processed_tensor)

        if len(frame_buffer_for_model) == SEQUENCE_LENGTH:
            input_sequence_tchw = torch.stack(list(frame_buffer_for_model), dim=0)
            model_input_bcthw = input_sequence_tchw.permute(1, 0, 2, 3).unsqueeze(0).to(device)
            with torch.no_grad():
                rppg_model_output = model(model_input_bcthw)
                new_rppg_segment_points = rppg_model_output[0, -1, :].cpu().numpy()
            if new_rppg_segment_points.size > 0:
                rppg_plot_buffer.append(new_rppg_segment_points[-1])

        if time.time() - last_hr_update_time >= HR_UPDATE_INTERVAL_SECONDS:
            if len(rppg_plot_buffer) >= HR_CALC_WINDOW_RPPG_LEN:
                rppg_segment_for_hr = np.array(list(rppg_plot_buffer))[-HR_CALC_WINDOW_RPPG_LEN:]
                if np.std(rppg_segment_for_hr) > 1e-6:
                    rppg_segment_for_hr_norm = (rppg_segment_for_hr - np.mean(rppg_segment_for_hr)) / np.std(
                        rppg_segment_for_hr)
                else:
                    rppg_segment_for_hr_norm = rppg_segment_for_hr - np.mean(rppg_segment_for_hr)
                calculated_hr = pulse_rate_from_power_spectral_density(
                    rppg_segment_for_hr_norm, FS=CAMERA_FPS,
                    LL_PR=40, UL_PR=180, BUTTER_ORDER=6, DETREND=False
                )
                if calculated_hr is not np.nan and calculated_hr is not None:
                    current_hr_numeric = calculated_hr
                    hr_history_buffer.append(current_hr_numeric)
                last_hr_update_time = time.time()

        with lock:
            g_current_hr_display_val = f"{current_hr_numeric:.2f} BPM"

        # --- 生成rPPG波形图 ---
        rppg_waveform_img_np = np.full((PLOT_HEIGHT_RPPG, PLOT_WIDTH_RPPG, 3), PLOT_BG_COLOR, dtype=np.uint8)
        plot_area_width = PLOT_WIDTH_RPPG - plot_margin_left - plot_margin_right
        plot_area_height = PLOT_HEIGHT_RPPG - plot_margin_top - plot_margin_bottom

        cv2.line(rppg_waveform_img_np, (plot_margin_left, plot_margin_top),
                 (plot_margin_left, plot_margin_top + plot_area_height), AXIS_COLOR, 1)
        cv2.line(rppg_waveform_img_np, (plot_margin_left, plot_margin_top + plot_area_height),
                 (plot_margin_left + plot_area_width, plot_margin_top + plot_area_height), AXIS_COLOR, 1)

        rppg_y_label_pil_y = plot_margin_top + plot_area_height // 2 - FONT_SIZE_AXIS_LABEL_PIL // 2 if AXIS_LABEL_FONT_PIL else plot_margin_top + plot_area_height // 2
        rppg_x_label_pil_y = PLOT_HEIGHT_RPPG - plot_margin_bottom + 5

        rppg_waveform_img_np = draw_text_pil_safe(rppg_waveform_img_np, "振幅", (5, rppg_y_label_pil_y),
                                                  AXIS_LABEL_FONT_PIL, TEXT_COLOR_DARK, cv2_axis_label_font_details)
        rppg_waveform_img_np = draw_text_pil_safe(rppg_waveform_img_np, f"时间 ({RPPG_DISPLAY_SECONDS}秒)",
                                                  (plot_margin_left + plot_area_width // 2 - 70, rppg_x_label_pil_y),
                                                  AXIS_LABEL_FONT_PIL, TEXT_COLOR_DARK, cv2_axis_label_font_details)

        if len(rppg_plot_buffer) > 1:
            points_to_plot_rppg = np.array(list(rppg_plot_buffer))
            if np.max(points_to_plot_rppg) - np.min(points_to_plot_rppg) > 1e-6:
                norm_points_rppg = (points_to_plot_rppg - np.min(points_to_plot_rppg)) / (
                            np.max(points_to_plot_rppg) - np.min(points_to_plot_rppg))
                pixel_y_values_rppg = plot_margin_top + (1 - norm_points_rppg) * plot_area_height
            else:
                pixel_y_values_rppg = np.full_like(points_to_plot_rppg, plot_margin_top + plot_area_height / 2)
            num_rppg_points = len(pixel_y_values_rppg)
            for i in range(num_rppg_points - 1):
                x1 = plot_margin_left + int(i * (plot_area_width / max(1, num_rppg_points - 1)))
                y1 = int(pixel_y_values_rppg[i])
                x2 = plot_margin_left + int((i + 1) * (plot_area_width / max(1, num_rppg_points - 1)))
                y2 = int(pixel_y_values_rppg[i + 1])
                cv2.line(rppg_waveform_img_np, (x1, y1), (x2, y2), RPPG_WAVE_COLOR, 1)

        # --- 生成心率趋势图 ---
        hr_trend_img_np = np.full((HR_PLOT_HEIGHT, HR_PLOT_WIDTH, 3), PLOT_BG_COLOR, dtype=np.uint8)
        cv2.line(hr_trend_img_np, (plot_margin_left, plot_margin_top),
                 (plot_margin_left, plot_margin_top + plot_area_height), AXIS_COLOR, 1)
        cv2.line(hr_trend_img_np, (plot_margin_left, plot_margin_top + plot_area_height),
                 (plot_margin_left + plot_area_width, plot_margin_top + plot_area_height), AXIS_COLOR, 1)
        estimated_hr_plot_duration = HR_PLOT_HISTORY_LEN * HR_UPDATE_INTERVAL_SECONDS

        hr_y_label_pil_y = plot_margin_top + plot_area_height // 2 - FONT_SIZE_AXIS_LABEL_PIL // 2 if AXIS_LABEL_FONT_PIL else plot_margin_top + plot_area_height // 2
        hr_x_label_pil_y = HR_PLOT_HEIGHT - plot_margin_bottom + 5

        hr_trend_img_np = draw_text_pil_safe(hr_trend_img_np, "BPM", (2, hr_y_label_pil_y), AXIS_LABEL_FONT_PIL,
                                             TEXT_COLOR_DARK, cv2_axis_label_font_details)
        hr_trend_img_np = draw_text_pil_safe(hr_trend_img_np, f"时间 ({estimated_hr_plot_duration}秒)",
                                             (plot_margin_left + plot_area_width // 2 - 80, hr_x_label_pil_y),
                                             AXIS_LABEL_FONT_PIL, TEXT_COLOR_DARK, cv2_axis_label_font_details)

        hr_text_to_draw_on_hr_graph = g_current_hr_display_val  # 使用全局更新的心率字符串
        if HR_FONT_PIL:
            text_width_pil_hr = HR_FONT_PIL.getlength(hr_text_to_draw_on_hr_graph)
            hr_text_x_on_hr_graph = HR_PLOT_WIDTH - plot_margin_right - int(text_width_pil_hr) - 10
            hr_text_y_on_hr_graph = plot_margin_top + 5
            hr_trend_img_np = draw_text_pil_safe(hr_trend_img_np, hr_text_to_draw_on_hr_graph,
                                                 (hr_text_x_on_hr_graph, hr_text_y_on_hr_graph), HR_FONT_PIL,
                                                 TEXT_COLOR_DARK, cv2_hr_on_graph_font_details)
        else:
            (text_width_cv2, text_height_cv2), _ = cv2.getTextSize(hr_text_to_draw_on_hr_graph,
                                                                   cv2_hr_on_graph_font_details[0],
                                                                   cv2_hr_on_graph_font_details[1],
                                                                   cv2_hr_on_graph_font_details[2])
            hr_text_x_on_hr_graph = HR_PLOT_WIDTH - plot_margin_right - text_width_cv2 - 10
            hr_text_y_on_hr_graph = plot_margin_top + text_height_cv2 + 5
            cv2.putText(hr_trend_img_np, hr_text_to_draw_on_hr_graph, (hr_text_x_on_hr_graph, hr_text_y_on_hr_graph),
                        cv2_hr_on_graph_font_details[0], cv2_hr_on_graph_font_details[1], TEXT_COLOR_DARK,
                        cv2_hr_on_graph_font_details[2])

        if len(hr_history_buffer) > 1:
            hr_points_to_plot_raw = np.array(list(hr_history_buffer))

            # --- 新增：对心率历史数据进行平滑 ---
            hr_points_to_plot_smoothed = hr_points_to_plot_raw  # 默认不平滑
            if len(hr_points_to_plot_raw) >= HR_SMOOTHING_WINDOW_SIZE:
                try:
                    # 确保窗口大小是奇数且小于等于数据长度
                    win_size = min(HR_SMOOTHING_WINDOW_SIZE, len(hr_points_to_plot_raw))
                    if win_size % 2 == 0: win_size -= 1  # 保证奇数
                    if win_size > HR_SMOOTHING_POLYORDER and win_size > 0:  # Savgol要求窗口大于polyorder
                        hr_points_to_plot_smoothed = savgol_filter(hr_points_to_plot_raw, win_size,
                                                                   HR_SMOOTHING_POLYORDER)
                except Exception as e_smooth:
                    print(f"心率平滑时出错: {e_smooth}")
                    # 出错则不平滑或使用简单移动平均


            hr_value_range_plot = HR_PLOT_Y_MAX - HR_PLOT_Y_MIN
            if hr_value_range_plot <= 0: hr_value_range_plot = 1
            hr_points_clamped = np.clip(hr_points_to_plot_smoothed, HR_PLOT_Y_MIN, HR_PLOT_Y_MAX)  # 使用平滑后的数据
            norm_hr_y = (hr_points_clamped - HR_PLOT_Y_MIN) / hr_value_range_plot
            pixel_y_values_hr = plot_margin_top + (1 - norm_hr_y) * plot_area_height
            num_hr_points_on_plot = len(pixel_y_values_hr)
            for i in range(num_hr_points_on_plot - 1):
                x1 = plot_margin_left + int(i * (plot_area_width / max(1, num_hr_points_on_plot - 1)))
                y1 = int(pixel_y_values_hr[i])
                x2 = plot_margin_left + int((i + 1) * (plot_area_width / max(1, num_hr_points_on_plot - 1)))
                y2 = int(pixel_y_values_hr[i + 1])
                cv2.line(hr_trend_img_np, (x1, y1), (x2, y2), HR_LINE_COLOR, 2)

            num_y_ticks = 5
            for i in range(num_y_ticks + 1):
                val = HR_PLOT_Y_MIN + i * (hr_value_range_plot / num_y_ticks)
                y_pos_line = plot_margin_top + plot_area_height - int(i * (plot_area_height / num_y_ticks))
                cv2.line(hr_trend_img_np, (plot_margin_left - TICK_LENGTH, y_pos_line), (plot_margin_left, y_pos_line),
                         AXIS_COLOR, 1)
                y_pos_text_pil = y_pos_line - FONT_SIZE_TICK_LABEL_PIL // 2 if TICK_LABEL_FONT_PIL else y_pos_line
                hr_trend_img_np = draw_text_pil_safe(hr_trend_img_np, f"{val:.0f}",
                                                     (plot_margin_left - 30, y_pos_text_pil), TICK_LABEL_FONT_PIL,
                                                     TEXT_COLOR_LIGHT, cv2_tick_label_font_details)

            hr_x_tick_label_pil_y = plot_margin_top + plot_area_height + 5
            hr_trend_img_np = draw_text_pil_safe(hr_trend_img_np, "0秒", (plot_margin_left, hr_x_tick_label_pil_y),
                                                 TICK_LABEL_FONT_PIL, TEXT_COLOR_LIGHT, cv2_tick_label_font_details)
            hr_trend_img_np = draw_text_pil_safe(hr_trend_img_np, f"~{estimated_hr_plot_duration}秒",
                                                 (plot_margin_left + plot_area_width - 40, hr_x_tick_label_pil_y),
                                                 TICK_LABEL_FONT_PIL, TEXT_COLOR_LIGHT, cv2_tick_label_font_details)

        with lock:
            # output_frame_webcam = display_frame_np.copy()
            output_frame_rppg = rppg_waveform_img_np.copy()
            output_frame_hr_trend = hr_trend_img_np.copy()

    cap.release()
    print("后台处理线程已停止。")


def generate_frames(frame_type):
    global output_frame_webcam, output_frame_rppg, output_frame_hr_trend, lock, AXIS_LABEL_FONT_PIL

    placeholder_width = 400
    placeholder_height = 200
    if frame_type == 'webcam':
        placeholder_height = 300
    elif frame_type == 'rppg':
        placeholder_height = PLOT_HEIGHT_RPPG
    elif frame_type == 'hr_trend':
        placeholder_height = HR_PLOT_HEIGHT

    placeholder_img_base = np.full((placeholder_height, placeholder_width, 3), PLOT_BG_COLOR, dtype=np.uint8)
    loading_text = "加载中..."

    cv2_placeholder_font_details = (cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)

    while True:
        time.sleep(1 / CAMERA_FPS)
        frame_to_encode = None
        with lock:
            if frame_type == 'webcam':
                frame_to_encode = output_frame_webcam
            elif frame_type == 'rppg':
                frame_to_encode = output_frame_rppg
            elif frame_type == 'hr_trend':
                frame_to_encode = output_frame_hr_trend

        current_placeholder = placeholder_img_base.copy()

        text_x_placeholder = int(placeholder_width / 2)
        text_y_placeholder = int(placeholder_height / 2)

        if AXIS_LABEL_FONT_PIL:
            try:
                text_length = AXIS_LABEL_FONT_PIL.getlength(loading_text)
            except AttributeError:
                text_width_pil, _ = AXIS_LABEL_FONT_PIL.getsize(loading_text)
                text_length = text_width_pil
            text_x_placeholder -= int(text_length / 2)
            text_y_placeholder -= FONT_SIZE_AXIS_LABEL_PIL // 2
            current_placeholder = draw_text_pil_safe(current_placeholder, loading_text,
                                                     (text_x_placeholder, text_y_placeholder), AXIS_LABEL_FONT_PIL,
                                                     TEXT_COLOR_DARK, cv2_placeholder_font_details)
        else:
            (text_w, text_h), _ = cv2.getTextSize("Loading...", cv2_placeholder_font_details[0],
                                                  cv2_placeholder_font_details[1], cv2_placeholder_font_details[2])
            text_x_placeholder -= text_w // 2
            text_y_placeholder += text_h // 2
            cv2.putText(current_placeholder, "Loading...", (text_x_placeholder, text_y_placeholder),
                        cv2_placeholder_font_details[0], cv2_placeholder_font_details[1], TEXT_COLOR_DARK,
                        cv2_placeholder_font_details[2])

        if frame_to_encode is None:
            (flag, encodedImage) = cv2.imencode(".jpg", current_placeholder)
        else:
            (flag, encodedImage) = cv2.imencode(".jpg", frame_to_encode)
        if not flag:
            (flag, encodedImage) = cv2.imencode(".jpg", current_placeholder)
            if not flag: continue
        yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + bytearray(encodedImage) + b'\r\n')


@app.route('/')
def index(): return render_template('index.html')


@app.route('/video_feed')
def video_feed(): return Response(generate_frames('webcam'), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/rppg_waveform_feed')
def rppg_waveform_feed(): return Response(generate_frames('rppg'), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/hr_trend_feed')
def hr_trend_feed(): return Response(generate_frames('hr_trend'), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/upload_frame', methods=['POST'])
def upload_frame():
    global output_frame_webcam
    try:
        # 验证请求内容
        if 'frame' not in request.files:
            return jsonify({"status": "error", "message": "No file part"}), 400
            
        file = request.files['frame']
        if file.filename == '':
            return jsonify({"status": "error", "message": "No selected file"}), 400
        
        # 读取图像数据
        img_bytes = file.read()
        np_arr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        # frame = cv2.resize(frame, (640, 480))  # 统一处理尺寸
        if frame is None:
            return jsonify({"status": "error", "message": "Invalid image"}), 400
        # 镜像翻转以匹配前端显示
        frame = cv2.flip(frame, 1)
        # 更新全局帧变量
        with lock:
            output_frame_webcam = frame.copy()
        # 处理逻辑（示例：返回图像尺寸）
        return jsonify({
            "status": "success",
            "width": frame.shape[1],
            "height": frame.shape[0]
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

 
def parseArgs():
    parser = argparse.ArgumentParser(description='实时rPPG Web演示应用')
    parser.add_argument('--device', type=str, default='cuda',
                        help="运行设备的名称: 'cuda' 或 'cpu'.")
    parser.add_argument('--model_path', type=str, default="./epoch30.pt", help="预训练PhysNet权重的路径。")
    parser.add_argument('--font_path', type=str, default="msyh.ttf",
                        help="中文字体文件的路径 (例如 simsun.ttc, msyh.ttf)。")
    parser.add_argument('--port', type=int, default=8800, help="Web服务器运行的端口号。")
    return parser.parse_args()


if __name__ == '__main__':
    g_args = parseArgs()
    try:
        FONT_PATH_TO_LOAD = g_args.font_path
        if not os.path.isabs(FONT_PATH_TO_LOAD) and not os.path.exists(FONT_PATH_TO_LOAD):
            script_dir = os.path.dirname(os.path.abspath(__file__))
            if not script_dir:
                script_dir = "."
            FONT_PATH_TO_LOAD = os.path.join(script_dir, g_args.font_path)

        if not os.path.exists(FONT_PATH_TO_LOAD):
            system_font_path_win = "C:/Windows/Fonts/msyh.ttc"
            system_font_path_linux = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            if os.path.exists(system_font_path_win):
                FONT_PATH_TO_LOAD = system_font_path_win
                print(f"指定字体 '{g_args.font_path}' 未找到, 尝试使用系统字体: {FONT_PATH_TO_LOAD}")
            elif os.path.exists(system_font_path_linux):
                FONT_PATH_TO_LOAD = system_font_path_linux
                print(f"指定字体 '{g_args.font_path}' 未找到, 尝试使用系统字体: {FONT_PATH_TO_LOAD}")
            else:
                raise IOError(f"Font file not found: {g_args.font_path} and common system fonts not found.")

        AXIS_LABEL_FONT_PIL = ImageFont.truetype(FONT_PATH_TO_LOAD, FONT_SIZE_AXIS_LABEL_PIL)
        TICK_LABEL_FONT_PIL = ImageFont.truetype(FONT_PATH_TO_LOAD, FONT_SIZE_TICK_LABEL_PIL)
        HR_FONT_PIL = ImageFont.truetype(FONT_PATH_TO_LOAD, FONT_SIZE_HR_ON_GRAPH_PIL)
        print(f"中文字体已从 '{FONT_PATH_TO_LOAD}' 加载。")
    except IOError as e:
        print(f"警告: 字体文件加载失败 ({e})。")
        AXIS_LABEL_FONT_PIL = None
        TICK_LABEL_FONT_PIL = None
        HR_FONT_PIL = None

    if g_args.device is None:
        g_args.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    else:
        g_args.device = torch.device(g_args.device)
    print(f"主线程: 准备在设备 {g_args.device} 上运行rPPG处理...")
    rppg_thread = threading.Thread(target=rppg_processing_loop, args=(g_args,), daemon=True)
    rppg_thread.start()
    print(f"Flask服务器将在 http://0.0.0.0:{g_args.port}/ 上运行")
    app.run(host='0.0.0.0', port=g_args.port, debug=False, threaded=True, use_reloader=False,ssl_context='adhoc')

