import os
import pickle
import numpy as np
import torch
from tqdm import tqdm

from utils import extract_video, pulse_rate_from_power_spectral_density
from utils_sig import *

def eval_rgb_rf_model( model, sequence_length1=64,

                      file_name="rgbd_rgb", ):
    model.eval()
    video_samples = []
    cur_est_ppgs = None # Initialize as None
    video_samples1 = []
    cur_video_sample = {}
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = model.to(device)
    # Prepare RGB and RF data in advance to avoid repeated loading
    # for cur_session in session_names:
    #     video_sample = {"video_path": os.path.join(rgb_root_dir, cur_session)}
    #     video_samples.append(video_sample)

    # Iterate over each session




    frames = extract_video(path='D:/code/RIT/video/video10')


    rgb_frame_num = frames.shape[0]

    print(f"frames:{frames.shape}")
    # Iterate over each sequence
    cur_rgb_cat_frames = None
    # 内层循环：遍历每一帧
    for cur_frame_num in range(0, rgb_frame_num):


        # Process RGB frames

        # 处理RGB帧
        cur_rgb_frames = frames[cur_frame_num, :, :, :]
        cur_frame_cropped = torch.from_numpy(cur_rgb_frames.astype(np.uint8)).permute(2, 0, 1).float()
        cur_frame_cropped = cur_frame_cropped / 255.0  # 归一化
        cur_frame_cropped = cur_frame_cropped.unsqueeze(0).to(device)
        # Concatenate RGB frames
        if cur_frame_num % sequence_length1 ==0:
            cur_rgb_cat_frames = cur_frame_cropped
        else:
            # print(f"cur_rgb_cat_frames:{cur_rgb_cat_frames.shape}, cur_frame_cropped:{cur_frame_cropped.shape}")
            cur_rgb_cat_frames = torch.cat((cur_rgb_cat_frames, cur_frame_cropped), dim=0)
        # print(f"rgb:{cur_rgb_cat_frames.shape[0]}")

    # Pass through the model



            # Test the performance
        if cur_rgb_cat_frames.shape[0] == sequence_length1:
            with torch.no_grad():
                # rgb
                cur_rgb_cat_frames = cur_rgb_cat_frames.unsqueeze(0).to(device)
                cur_rgb_cat_frames = torch.transpose(cur_rgb_cat_frames, 1, 2)


                # print(f"cur:{len(cur_rgb_cat_frames)}")
                # print(f"IQ:{len(IQ_frames)}")
                # print(f"cur:{cur_rgb_cat_frames.shape}")
                # print(f"IQ:{IQ_frames.shape}")

                # print(f"cur_rgb_cat_frames:{cur_rgb_cat_frames}")
                cur_est_ppg = model(cur_rgb_cat_frames)[:, -1, :]
                # print(f"cur_est_ppg:{cur_est_ppg}")
                cur_est_ppg = cur_est_ppg[0].detach().cpu().numpy()

            if cur_est_ppgs is None:
                cur_est_ppgs = cur_est_ppg
            else:
                cur_est_ppgs = np.concatenate((cur_est_ppgs, cur_est_ppg), -1)
    # print(f"cur_est_ppgs:{cur_est_ppgs}")
        # print("cur_est_ppg",cur_est_ppg)
    # save
    cur_video_sample['est_ppgs'] = cur_est_ppgs

    video_samples1.append(cur_video_sample)


    print('All finished!')
    # Evaluate performance
    hr_window_size = 100
    stride = 10
    mae_list = []
    all_hr_est = []
    all_hr_gt = []
    # print(f"Number of video samples: {len(video_samples1)}")
    # print(f"Sample video data (first 5 items): {video_samples[:5]}")



    cur_est_ppgs = cur_video_sample['est_ppgs']
    # print(f"len(cur_est_ppgs):{len(cur_est_ppgs)}")
    # Ensure cur_est_ppgs and cur_gt_ppgs are not empty




    # Ensure cur_est_ppgs and cur_gt_ppgs are not empty


    cur_est_ppgs = (cur_est_ppgs - np.mean(cur_est_ppgs)) / np.std(cur_est_ppgs)



    # hr1 = sig_out_hr_batch([cur_est_ppgs], 0.6, 4, 30, order=2)
    # print(hr1)
    all_ppg_est = []
    all_ppg_gt = []
    # Get est HR for each window
    hr_est_temp = []

    # print(len(cur_est_ppgs))
    # print("hh", len(cur_est_ppgs) - hr_window_size)
    for start in range(0, len(cur_est_ppgs) - hr_window_size, stride):
        ppg_est_window = cur_est_ppgs[start:start + hr_window_size]


        # 打印每个窗口的数据，检查是否为空
        # print(f"ppg_est_window: {ppg_est_window}, ppg_gt_window: {ppg_gt_window}")

        ppg_est_window = (ppg_est_window - np.mean(ppg_est_window)) / np.std(ppg_est_window)

        hr_est_temp.append(pulse_rate_from_power_spectral_density(
            ppg_est_window, 30, 45, 150, BUTTER_ORDER=6, DETREND=False))

        all_ppg_est.append(ppg_est_window)

    # print(f"hr_est: {hr_est_temp}, hr_gt: {hr_gt_temp}")
    hr_est_windowed = np.array([hr_est_temp])

    # print(f"hr_est_temp: {hr_est_temp}, hr_gt_temp: {hr_gt_temp}")
    all_hr_est.append(hr_est_windowed)


    # Errors
    # _, MAE, _, _ = getErrors(hr_est_windowed, hr_gt_windowed)

    # mae_list.append(MAE)
    # print('Mean MAE:', np.mean(np.array(mae_list)))
    return  hr_est_windowed,  all_ppg_est


