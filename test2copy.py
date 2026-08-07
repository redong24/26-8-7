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
import uuid
from flask import Flask, json, jsonify, render_template, Response, request, make_response
from PIL import Image, ImageDraw, ImageFont
from scipy.signal import savgol_filter

try:
    from PhysNetModel import PhysNet
    from utils import pulse_rate_from_power_spectral_density
except ImportError as e:
    print(f"导入自定义模块失败: {e}")
    print("请确保 PhysNetModel.py 和 utils.py 文件在正确的路径下。")
    sys.exit(1)

os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'

# 常量定义
SEQUENCE_LENGTH = 16
CAMERA_FPS = 30
FACE_TARGET_SIZE = (64, 64)

RPPG_DISPLAY_SECONDS = 10
# RPPG_PLOT_BUFFER_MAX_LEN = RPPG_DISPLAY_SECONDS * CAMERA_FPS
RPPG_PLOT_BUFFER_MAX_LEN = 45
PLOT_WIDTH_RPPG = 400
PLOT_HEIGHT_RPPG = 200

HR_CALC_WINDOW_SECONDS = 2
# HR_CALC_WINDOW_RPPG_LEN = HR_CALC_WINDOW_SECONDS * CAMERA_FPS
HR_CALC_WINDOW_RPPG_LEN = 45
# HR_UPDATE_INTERVAL_SECONDS = 1
HR_UPDATE_INTERVAL_SECONDS = 0.5

HR_PLOT_HISTORY_LEN = 60
HR_PLOT_WIDTH = 400
HR_PLOT_HEIGHT = 200
HR_PLOT_Y_MIN = 40
HR_PLOT_Y_MAX = 180
HR_SMOOTHING_WINDOW_SIZE = 5
HR_SMOOTHING_POLYORDER = 2

# 颜色和样式常量
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

class ClientSession:
    def __init__(self, session_id, args, shared_fonts, model):
        self.session_id = session_id
        self.args = args
        self.shared_fonts = shared_fonts
        self.lock = threading.Lock()
        self.model = model  # 使用共享模型
        # 视频帧和处理结果
        self.output_frame_webcam = None
        self.output_frame_rppg = None
        self.output_frame_hr_trend = None
        self.current_hr_display_val = "0"
        
        # 处理状态
        self.frame_buffer_for_model = collections.deque(maxlen=SEQUENCE_LENGTH)
        self.rppg_plot_buffer = collections.deque(maxlen=RPPG_PLOT_BUFFER_MAX_LEN)
        self.hr_history_buffer = collections.deque(maxlen=HR_PLOT_HISTORY_LEN)
        self.current_hr_numeric = 0.0
        self.last_hr_update_time = time.time()
        self.last_activity_time = time.time()
        
        # 模型和检测器
        self.detector = MTCNN()
        
        # 线程控制
        self.running = True
        self.processing_thread = threading.Thread(target=self.rppg_processing_loop, daemon=True)
        self.processing_thread.start()
    
    def update_activity(self):
        """更新会话最后活动时间"""
        self.last_activity_time = time.time()
    
    def stop(self):
        """停止会话"""
        self.running = False
        if self.processing_thread.is_alive():
            self.processing_thread.join()
        print(f"会话 {self.session_id} 已停止")
    
    def is_active(self, timeout=1800):
        """检查会话是否活跃(默认30分钟超时)"""
        return (time.time() - self.last_activity_time) < timeout
    
    def draw_text_pil_safe(self, image_np, text, position, font_pil, color_bgr, fallback_font_details=None):
        """安全地使用PIL绘制文本，失败时回退到OpenCV"""
        if font_pil:
            try:
                image_pil = Image.fromarray(cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB))
                draw = ImageDraw.Draw(image_pil)
                color_rgb = (color_bgr[2], color_bgr[1], color_bgr[0])
                draw.text(position, text, font=font_pil, fill=color_rgb)
                return cv2.cvtColor(np.array(image_pil), cv2.COLOR_RGB2BGR)
            except Exception as e:
                print(f"会话 {self.session_id} Pillow绘制文本 '{text}' 时出错: {e}")
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
    
    def rppg_processing_loop(self):
        """rPPG处理主循环"""
        if not self.model:
            return
        
        print(f"会话 {self.session_id}: rPPG处理线程启动")
        
        # 绘图相关参数
        plot_margin_left = 45
        plot_margin_bottom = 35
        plot_margin_top = 25
        plot_margin_right = 15
        
        # OpenCV字体回退设置
        cv2_axis_label_font_details = (cv2.FONT_HERSHEY_SIMPLEX, AXIS_LABEL_FONT_SCALE_CV2, LABEL_FONT_THICKNESS)
        cv2_tick_label_font_details = (cv2.FONT_HERSHEY_SIMPLEX, LABEL_FONT_SCALE_CV2 - 0.05, LABEL_FONT_THICKNESS)
        cv2_hr_on_graph_font_details = (
            cv2.FONT_HERSHEY_SIMPLEX, HR_TEXT_FONT_SCALE_ON_GRAPH_CV2, HR_TEXT_FONT_THICKNESS_ON_GRAPH_CV2)
        
        while self.running:
            try:
                # 从会话获取最新帧
                with self.lock:
                    if self.output_frame_webcam is None:
                        time.sleep(0.01)
                        continue
                    frame = self.output_frame_webcam.copy()
                
                # 人脸检测和处理
                display_frame_np = frame.copy()
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                faces = self.detector.detect_faces(frame_rgb)
                face_roi_processed_tensor = None
                
                if faces:
                    face_data = faces[0]
                    x, y, w, h = face_data['box']
                    x, y = max(0, x), max(0, y)
                    face_roi_bgr = frame[y:min(y + h, frame.shape[0] - 1), x:min(x + w, frame.shape[1] - 1)]
                    if face_roi_bgr.size > 0:
                        # cv2.rectangle(display_frame_np, (x, y), (x + w, y + h), (0, 255, 0), 2)
                        face_roi_resized = cv2.resize(face_roi_bgr, FACE_TARGET_SIZE, interpolation=cv2.INTER_AREA)
                        face_roi_normalized = face_roi_resized / 255.0
                        face_tensor_chw = torch.from_numpy(face_roi_normalized).permute(2, 0, 1).float()
                        face_roi_processed_tensor = face_tensor_chw
                
                # 更新帧缓冲区
                if face_roi_processed_tensor is not None:
                    self.frame_buffer_for_model.append(face_roi_processed_tensor)
                
                # 当缓冲区足够时进行rPPG预测
                if len(self.frame_buffer_for_model) == SEQUENCE_LENGTH:
                    input_sequence_tchw = torch.stack(list(self.frame_buffer_for_model), dim=0)
                    model_input_bcthw = input_sequence_tchw.permute(1, 0, 2, 3).unsqueeze(0).to(self.args.device)
                    with torch.no_grad():
                        rppg_model_output = self.model(model_input_bcthw)
                        new_rppg_segment_points = rppg_model_output[0, -1, :].cpu().numpy()
                    if new_rppg_segment_points.size > 0:
                        self.rppg_plot_buffer.append(new_rppg_segment_points[-1])
                
                # 定期更新心率
                if time.time() - self.last_hr_update_time >= HR_UPDATE_INTERVAL_SECONDS:
                    if len(self.rppg_plot_buffer) >= HR_CALC_WINDOW_RPPG_LEN:
                        rppg_segment_for_hr = np.array(list(self.rppg_plot_buffer))[-HR_CALC_WINDOW_RPPG_LEN:]
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
                            self.current_hr_numeric = calculated_hr
                            self.hr_history_buffer.append(self.current_hr_numeric)
                        self.last_hr_update_time = time.time()
                
                # 更新显示的心率值
                with self.lock:
                    self.current_hr_display_val = f"{self.current_hr_numeric:.2f}"
                
                # --- 生成rPPG波形图 ---
                rppg_waveform_img_np = np.full((PLOT_HEIGHT_RPPG, PLOT_WIDTH_RPPG, 3), PLOT_BG_COLOR, dtype=np.uint8)
                plot_area_width = PLOT_WIDTH_RPPG - plot_margin_left - plot_margin_right
                plot_area_height = PLOT_HEIGHT_RPPG - plot_margin_top - plot_margin_bottom
                
                # 绘制坐标轴
                cv2.line(rppg_waveform_img_np, (plot_margin_left, plot_margin_top),
                         (plot_margin_left, plot_margin_top + plot_area_height), AXIS_COLOR, 1)
                cv2.line(rppg_waveform_img_np, (plot_margin_left, plot_margin_top + plot_area_height),
                         (plot_margin_left + plot_area_width, plot_margin_top + plot_area_height), AXIS_COLOR, 1)
                
                # 添加轴标签
                rppg_y_label_pil_y = plot_margin_top + plot_area_height // 2 - FONT_SIZE_AXIS_LABEL_PIL // 2 if self.shared_fonts['axis_label'] else plot_margin_top + plot_area_height // 2
                rppg_x_label_pil_y = PLOT_HEIGHT_RPPG - plot_margin_bottom + 5
                
                rppg_waveform_img_np = self.draw_text_pil_safe(
                    rppg_waveform_img_np, "AM", (5, rppg_y_label_pil_y),
                    self.shared_fonts['axis_label'], TEXT_COLOR_DARK, cv2_axis_label_font_details)
                rppg_waveform_img_np = self.draw_text_pil_safe(
                    rppg_waveform_img_np, f"time ({RPPG_DISPLAY_SECONDS}s)",
                    (plot_margin_left + plot_area_width // 2 - 70, rppg_x_label_pil_y),
                    self.shared_fonts['axis_label'], TEXT_COLOR_DARK, cv2_axis_label_font_details)
                
                # 绘制rPPG波形
                if len(self.rppg_plot_buffer) > 1:
                    points_to_plot_rppg = np.array(list(self.rppg_plot_buffer))
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
                
                # 添加轴标签
                hr_y_label_pil_y = plot_margin_top + plot_area_height // 2 - FONT_SIZE_AXIS_LABEL_PIL // 2 if self.shared_fonts['axis_label'] else plot_margin_top + plot_area_height // 2
                hr_x_label_pil_y = HR_PLOT_HEIGHT - plot_margin_bottom + 5
                
                hr_trend_img_np = self.draw_text_pil_safe(
                    hr_trend_img_np, "BPM", (2, hr_y_label_pil_y), 
                    self.shared_fonts['axis_label'], TEXT_COLOR_DARK, cv2_axis_label_font_details)
                hr_trend_img_np = self.draw_text_pil_safe(
                    hr_trend_img_np, f"time ({estimated_hr_plot_duration}s)",
                    (plot_margin_left + plot_area_width // 2 - 80, hr_x_label_pil_y),
                    self.shared_fonts['axis_label'], TEXT_COLOR_DARK, cv2_axis_label_font_details)
                
                # 添加心率数值显示
                hr_text_to_draw_on_hr_graph = self.current_hr_display_val
                if self.shared_fonts['hr']:
                    try:
                        text_width_pil_hr = self.shared_fonts['hr'].getlength(hr_text_to_draw_on_hr_graph)
                    except AttributeError:
                        text_width_pil_hr, _ = self.shared_fonts['hr'].getsize(hr_text_to_draw_on_hr_graph)
                    hr_text_x_on_hr_graph = HR_PLOT_WIDTH - plot_margin_right - int(text_width_pil_hr) - 10
                    hr_text_y_on_hr_graph = plot_margin_top + 5
                    hr_trend_img_np = self.draw_text_pil_safe(
                        hr_trend_img_np, hr_text_to_draw_on_hr_graph,
                        (hr_text_x_on_hr_graph, hr_text_y_on_hr_graph), 
                        self.shared_fonts['hr'], TEXT_COLOR_DARK, cv2_hr_on_graph_font_details)
                else:
                    (text_width_cv2, text_height_cv2), _ = cv2.getTextSize(
                        hr_text_to_draw_on_hr_graph,
                        cv2_hr_on_graph_font_details[0],
                        cv2_hr_on_graph_font_details[1],
                        cv2_hr_on_graph_font_details[2])
                    hr_text_x_on_hr_graph = HR_PLOT_WIDTH - plot_margin_right - text_width_cv2 - 10
                    hr_text_y_on_hr_graph = plot_margin_top + text_height_cv2 + 5
                    cv2.putText(
                        hr_trend_img_np, hr_text_to_draw_on_hr_graph, 
                        (hr_text_x_on_hr_graph, hr_text_y_on_hr_graph),
                        cv2_hr_on_graph_font_details[0], cv2_hr_on_graph_font_details[1], 
                        TEXT_COLOR_DARK, cv2_hr_on_graph_font_details[2])
                
                # 绘制心率趋势线
                if len(self.hr_history_buffer) > 1:
                    hr_points_to_plot_raw = np.array(list(self.hr_history_buffer))
                    
                    # 对心率数据进行平滑处理
                    hr_points_to_plot_smoothed = hr_points_to_plot_raw
                    if len(hr_points_to_plot_raw) >= HR_SMOOTHING_WINDOW_SIZE:
                        try:
                            win_size = min(HR_SMOOTHING_WINDOW_SIZE, len(hr_points_to_plot_raw))
                            if win_size % 2 == 0: win_size -= 1
                            if win_size > HR_SMOOTHING_POLYORDER and win_size > 0:
                                hr_points_to_plot_smoothed = savgol_filter(
                                    hr_points_to_plot_raw, win_size, HR_SMOOTHING_POLYORDER)
                        except Exception as e_smooth:
                            print(f"会话 {self.session_id} 心率平滑时出错: {e_smooth}")
                    
                    hr_value_range_plot = HR_PLOT_Y_MAX - HR_PLOT_Y_MIN
                    if hr_value_range_plot <= 0: hr_value_range_plot = 1
                    hr_points_clamped = np.clip(hr_points_to_plot_smoothed, HR_PLOT_Y_MIN, HR_PLOT_Y_MAX)
                    norm_hr_y = (hr_points_clamped - HR_PLOT_Y_MIN) / hr_value_range_plot
                    pixel_y_values_hr = plot_margin_top + (1 - norm_hr_y) * plot_area_height
                    num_hr_points_on_plot = len(pixel_y_values_hr)
                    
                    # 绘制趋势线
                    for i in range(num_hr_points_on_plot - 1):
                        x1 = plot_margin_left + int(i * (plot_area_width / max(1, num_hr_points_on_plot - 1)))
                        y1 = int(pixel_y_values_hr[i])
                        x2 = plot_margin_left + int((i + 1) * (plot_area_width / max(1, num_hr_points_on_plot - 1)))
                        y2 = int(pixel_y_values_hr[i + 1])
                        cv2.line(hr_trend_img_np, (x1, y1), (x2, y2), HR_LINE_COLOR, 2)
                    
                    # 绘制Y轴刻度
                    num_y_ticks = 5
                    for i in range(num_y_ticks + 1):
                        val = HR_PLOT_Y_MIN + i * (hr_value_range_plot / num_y_ticks)
                        y_pos_line = plot_margin_top + plot_area_height - int(i * (plot_area_height / num_y_ticks))
                        cv2.line(
                            hr_trend_img_np, 
                            (plot_margin_left - TICK_LENGTH, y_pos_line), 
                            (plot_margin_left, y_pos_line),
                            AXIS_COLOR, 1)
                        y_pos_text_pil = y_pos_line - FONT_SIZE_TICK_LABEL_PIL // 2 if self.shared_fonts['tick_label'] else y_pos_line
                        hr_trend_img_np = self.draw_text_pil_safe(
                            hr_trend_img_np, f"{val:.0f}",
                            (plot_margin_left - 30, y_pos_text_pil),
                            self.shared_fonts['tick_label'], TEXT_COLOR_LIGHT, cv2_tick_label_font_details)
                    
                    # 绘制X轴刻度
                    hr_x_tick_label_pil_y = plot_margin_top + plot_area_height + 5
                    hr_trend_img_np = self.draw_text_pil_safe(
                        hr_trend_img_np, "0s", (plot_margin_left, hr_x_tick_label_pil_y),
                        self.shared_fonts['tick_label'], TEXT_COLOR_LIGHT, cv2_tick_label_font_details)
                    hr_trend_img_np = self.draw_text_pil_safe(
                        hr_trend_img_np, f"~{estimated_hr_plot_duration}s",
                        (plot_margin_left + plot_area_width - 40, hr_x_tick_label_pil_y),
                        self.shared_fonts['tick_label'], TEXT_COLOR_LIGHT, cv2_tick_label_font_details)
                
                # 更新输出帧
                with self.lock:
                    self.output_frame_rppg = rppg_waveform_img_np.copy()
                    self.output_frame_hr_trend = hr_trend_img_np.copy()
            
            except Exception as e:
                print(f"会话 {self.session_id} 处理循环中发生错误: {e}")
                time.sleep(1)
        
        print(f"会话 {self.session_id}: rPPG处理线程退出")

class RPGGApplication:
    def __init__(self, args):
        self.args = args
        self.sessions = {}
        self.session_lock = threading.Lock()
        self.shared_fonts = self._init_fonts()
        # 加载全局模型单例
        self.model = self._load_model()
        # 启动会话清理线程
        self.cleanup_thread = threading.Thread(target=self._cleanup_inactive_sessions, daemon=True)
        self.cleanup_thread.start()

    def _load_model(self):
        """加载全局PhysNet模型单例"""
        model = PhysNet(S=2, in_ch=3)
        try:
            model.load_state_dict(torch.load(self.args.model_path, map_location=self.args.device))
            print(f"模型已从 {self.args.model_path} 加载")
        except Exception as e:
            print(f"加载模型时出错: {e}")
            return None
        return model.to(self.args.device).eval()
    
    def _init_fonts(self):
        # """初始化共享字体资源"""
        # fonts = {
        #     'axis_label': None,
        #     'tick_label': None,
        #     'hr': None
        # }
        
        # try:
        #     FONT_PATH_TO_LOAD = self.args.font_path
        #     # 处理相对路径
        #     if not os.path.isabs(FONT_PATH_TO_LOAD) and not os.path.exists(FONT_PATH_TO_LOAD):
        #         script_dir = os.path.dirname(os.path.abspath(__file__))
        #         FONT_PATH_TO_LOAD = os.path.join(script_dir or ".", self.args.font_path)

        #     # 检查字体是否存在
        #     if not os.path.exists(FONT_PATH_TO_LOAD):
        #         # Ubuntu系统字体常见位置
        #         ubuntu_font_paths = [
        #             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",       # 默认安装的DejaVu字体
        #             "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",         # Ubuntu自带字体
        #             "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",       # 文泉驿微米黑
        #             "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc" # Google Noto字体
        #         ]
                
        #         # 尝试查找可用的系统字体
        #         for font_path in ubuntu_font_paths:
        #             if os.path.exists(font_path):
        #                 FONT_PATH_TO_LOAD = font_path
        #                 print(f"指定字体未找到，使用系统字体: {font_path}")
        #                 break
        #         else:
        #             # 如果常见字体都不存在，尝试用fc-match查找
        #             try:
        #                 import subprocess
        #                 result = subprocess.run(['fc-match', 'sans', '-f', '%{file}'], 
        #                                     capture_output=True, text=True)
        #                 if result.returncode == 0 and os.path.exists(result.stdout.strip()):
        #                     FONT_PATH_TO_LOAD = result.stdout.strip()
        #                     print(f"通过fc-match找到系统字体: {FONT_PATH_TO_LOAD}")
        #                 else:
        #                     raise IOError(f"字体文件未找到: {self.args.font_path}，且未找到合适的系统字体")
        #             except:
        #                 raise IOError(f"字体文件未找到: {self.args.font_path}，且字体匹配工具不可用")

        #     # 加载字体
        #     fonts = {
        #         'axis_label': ImageFont.truetype(FONT_PATH_TO_LOAD, FONT_SIZE_AXIS_LABEL_PIL),
        #         'tick_label': ImageFont.truetype(FONT_PATH_TO_LOAD, FONT_SIZE_TICK_LABEL_PIL),
        #         'hr': ImageFont.truetype(FONT_PATH_TO_LOAD, FONT_SIZE_HR_ON_GRAPH_PIL)
        #     }
        #     print(f"字体已从 '{FONT_PATH_TO_LOAD}' 加载。")

        # except (IOError, ImportError) as e:
        #     print(f"警告: 字体加载失败 ({e})，将使用OpenCV默认字体")
        # 设置备用字体方案
        fonts = {
            'axis_label': None,
            'tick_label': None,
            'hr': None
        }
            
        return fonts
    
    def create_session(self):
        """创建新的客户端会话"""
        session_id = str(uuid.uuid4())
        with self.session_lock:
            session = ClientSession(session_id, self.args, self.shared_fonts, self.model)
            self.sessions[session_id] = session
        print(f"创建新会话: {session_id}")
        return session_id
    
    def get_session(self, session_id):
        """获取现有会话"""
        with self.session_lock:
            return self.sessions.get(session_id)
    
    def cleanup_session(self, session_id):
        """清理指定会话"""
        with self.session_lock:
            session = self.sessions.pop(session_id, None)
            if session:
                session.stop()
                print(f"已清理会话: {session_id}")
    
    def _cleanup_inactive_sessions(self):
        """定期清理不活跃的会话"""
        while True:
            time.sleep(60)  # 每分钟检查一次
            with self.session_lock:
                to_remove = [sid for sid, session in self.sessions.items() if not session.is_active()]
                for sid in to_remove:
                    self.sessions[sid].stop()
                    del self.sessions[sid]
                if to_remove:
                    print(f"清理了 {len(to_remove)} 个不活跃会话")

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='实时rPPG Web演示应用')
    parser.add_argument('--device', type=str, default='cuda',
                      help="运行设备的名称: 'cuda' 或 'cpu'.")
    parser.add_argument('--model_path', type=str, default="./epoch30.pt", 
                      help="预训练PhysNet权重的路径。")
    parser.add_argument('--font_path', type=str, default="msyh.ttf",
                      help="中文字体文件的路径 (例如 simsun.ttc, msyh.ttf)。")
    parser.add_argument('--port', type=int, default=8800, 
                      help="Web服务器运行的端口号。")
    parser.add_argument('--max_sessions', type=int, default=10,
                      help="最大并发会话数。")
    return parser.parse_args()

# 创建Flask应用和RPGGApplication实例
app = Flask(__name__)
# args = parse_args()
# args.device = torch.device(args.device if torch.cuda.is_available() else 'cpu')
# rppg_app = RPGGApplication(args)

config = {
    'device': 'cuda' if torch.cuda.is_available() else 'cpu',
    'model_path': './epoch30.pt',
    'font_path': 'msyh.ttf',
    'max_sessions': 10
}

# 初始化你的RPGG应用
rppg_app = RPGGApplication(argparse.Namespace(**config))

@app.route('/')
def index():
    """主页路由，创建或恢复会话"""
    session_id = request.cookies.get('session_id')
    if not session_id or not rppg_app.get_session(session_id):
        # 创建新会话
        session_id = rppg_app.create_session()
        resp = make_response(render_template('test.html'))
        resp.set_cookie('session_id', session_id, max_age=3600)  # 1小时有效期
        return resp
    return render_template('test.html')

@app.route('/max')
def indexNew():
    """主页路由，创建或恢复会话"""
    session_id = request.cookies.get('session_id')
    if not session_id or not rppg_app.get_session(session_id):
        # 创建新会话
        session_id = rppg_app.create_session()
        resp = make_response(render_template('index.html'))
        resp.set_cookie('session_id', session_id, max_age=14400)  # 4小时有效期
        return resp
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    """视频流路由"""
    session_id = request.cookies.get('session_id')
    session = rppg_app.get_session(session_id)
    if not session:
        return Response("Invalid session", status=400)
    
    def generate():
        placeholder_width = 400
        placeholder_height = 300
        placeholder_img = np.full((placeholder_height, placeholder_width, 3), PLOT_BG_COLOR, dtype=np.uint8)
        loading_text = "加载中..."
        font_details = (cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        
        while True:
            # time.sleep(1 / CAMERA_FPS)
            time.sleep(1)
            frame_to_encode = None
            with session.lock:
                frame_to_encode = session.output_frame_webcam
            
            if frame_to_encode is None:
                current_placeholder = placeholder_img.copy()
                (text_w, text_h), _ = cv2.getTextSize(
                    loading_text, font_details[0], font_details[1], font_details[2])
                text_x = int((placeholder_width - text_w) / 2)
                text_y = int((placeholder_height + text_h) / 2)
                cv2.putText(
                    current_placeholder, loading_text, (text_x, text_y),
                    font_details[0], font_details[1], TEXT_COLOR_DARK, font_details[2])
                (flag, encoded) = cv2.imencode(".jpg", current_placeholder)
            else:
                (flag, encoded) = cv2.imencode(".jpg", frame_to_encode)
            
            if not flag:
                continue
            
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + bytearray(encoded) + b'\r\n')
    
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/rppg_waveform_feed')
def rppg_waveform_feed():
    """rPPG波形图路由"""
    session_id = request.cookies.get('session_id')
    session = rppg_app.get_session(session_id)
    if not session:
        return Response("Invalid session", status=400)
    
    def generate():
        placeholder_width = PLOT_WIDTH_RPPG
        placeholder_height = PLOT_HEIGHT_RPPG
        placeholder_img = np.full((placeholder_height, placeholder_width, 3), PLOT_BG_COLOR, dtype=np.uint8)
        loading_text = "加载中..."
        font_details = (cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        
        while True:
            time.sleep(1 / CAMERA_FPS)
            frame_to_encode = None
            with session.lock:
                frame_to_encode = session.output_frame_rppg
            
            if frame_to_encode is None:
                current_placeholder = placeholder_img.copy()
                (text_w, text_h), _ = cv2.getTextSize(
                    loading_text, font_details[0], font_details[1], font_details[2])
                text_x = int((placeholder_width - text_w) / 2)
                text_y = int((placeholder_height + text_h) / 2)
                cv2.putText(
                    current_placeholder, loading_text, (text_x, text_y),
                    font_details[0], font_details[1], TEXT_COLOR_DARK, font_details[2])
                (flag, encoded) = cv2.imencode(".jpg", current_placeholder)
            else:
                (flag, encoded) = cv2.imencode(".jpg", frame_to_encode)
            
            if not flag:
                continue
            
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + bytearray(encoded) + b'\r\n')
    
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/hr_trend_feed')
def hr_trend_feed():
    """心率趋势图路由"""
    session_id = request.cookies.get('session_id')
    session = rppg_app.get_session(session_id)
    if not session:
        return Response("Invalid session", status=400)
    
    def generate():
        placeholder_width = HR_PLOT_WIDTH
        placeholder_height = HR_PLOT_HEIGHT
        placeholder_img = np.full((placeholder_height, placeholder_width, 3), PLOT_BG_COLOR, dtype=np.uint8)
        loading_text = "加载中..."
        font_details = (cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        
        while True:
            time.sleep(1 / CAMERA_FPS)
            frame_to_encode = None
            with session.lock:
                frame_to_encode = session.output_frame_hr_trend
            
            if frame_to_encode is None:
                current_placeholder = placeholder_img.copy()
                (text_w, text_h), _ = cv2.getTextSize(
                    loading_text, font_details[0], font_details[1], font_details[2])
                text_x = int((placeholder_width - text_w) / 2)
                text_y = int((placeholder_height + text_h) / 2)
                cv2.putText(
                    current_placeholder, loading_text, (text_x, text_y),
                    font_details[0], font_details[1], TEXT_COLOR_DARK, font_details[2])
                (flag, encoded) = cv2.imencode(".jpg", current_placeholder)
            else:
                (flag, encoded) = cv2.imencode(".jpg", frame_to_encode)
            
            if not flag:
                continue
            
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + bytearray(encoded) + b'\r\n')
    
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/upload_frame', methods=['POST'])
def upload_frame():
    """上传视频帧路由"""
    session_id = request.cookies.get('session_id')
    session = rppg_app.get_session(session_id)
    if not session:
        return jsonify({"status": "error", "message": "Invalid session"}), 400
    
    try:
        frame_data = request.get_data()  # 获取未经处理的二进制流
        
        # 2. 转换为numpy数组
        nparr = np.frombuffer(frame_data, np.uint8)
        
        # 3. 解码为OpenCV图像格式 (BGR)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return jsonify({"status": "error", "message": "Invalid image"}), 400
        
        # 镜像翻转以匹配前端显示
        frame = cv2.flip(frame, 1)
        
        # 更新会话帧
        with session.lock:
            session.output_frame_webcam = frame.copy()
            session.update_activity()
            # # 初始化视频缓冲区
            # if not hasattr(session, 'video_buffer'):
            #     session.video_buffer = collections.deque(maxlen=600)  # 假设30fps，600帧=20秒
            #     session.video_start_time = time.time()
            #     session.video_params = {
            #         'width': frame.shape[1],
            #         'height': frame.shape[0],
            #         'fps': 30
            #     }
            
            # # 添加当前帧到缓冲区
            # session.video_buffer.append(frame.copy())
            
            # # 检查是否达到20秒
            # elapsed_time = time.time() - session.video_start_time
            # if elapsed_time >= 60.0 and not hasattr(session, 'video_saved'):
            #     # 保存视频
            #     timestamp = int(time.time())
            #     video_path = f"static/videos/session_{session_id}_{timestamp}.mp4"
            #     fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            #     out = cv2.VideoWriter(video_path, fourcc, session.video_params['fps'], 
            #                          (session.video_params['width'], session.video_params['height']))
            #     for buffered_frame in session.video_buffer:
            #         out.write(buffered_frame)
            #     out.release()
                
            #     session.video_saved = True
            #     session.video_path = video_path  # 保存视频路径供后续使用
        
        return jsonify({
            "status": "success",
            "width": frame.shape[1],
            "height": frame.shape[0]
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_hr')
def get_hr():
    """获取当前心率值"""
    session_id = request.cookies.get('session_id')
    session = rppg_app.get_session(session_id)
    if not session:
        return jsonify({"status": "error", "message": "Invalid session"}), 400
    
    with session.lock:
        session.update_activity()
        return jsonify({
            "status": "success",
            "heart_rate": session.current_hr_display_val
        })

if __name__ == '__main__':
    app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB   
    app.run(host='0.0.0.0', port='8800', debug=False, threaded=True, use_reloader=False, ssl_context='adhoc')