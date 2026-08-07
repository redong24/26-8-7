import cv2
import torch
from matplotlib import pyplot as plt
from torch.autograd import Variable

from utils_sig import *

from torch.utils.data.dataloader import default_collate
import torch.nn as nn
from TorchLossComputer import TorchLossComputer
import os
from scipy.stats import pearsonr
from torch.utils.data import Dataset
from scipy.signal import welch
from scipy.signal import resample
import pandas as pd


class Neg_Pearson(nn.Module):  # Pearson range [-1, 1] so if < 0, abs|loss| ; if >0, 1- loss
    def __init__(self):
        super(Neg_Pearson, self).__init__()
        return

    def forward(self, preds, labels):  # all variable operation
        loss = 0
        for i in range(preds.shape[0]):
            sum_x = torch.sum(preds[i])  # x
            sum_y = torch.sum(labels[i])  # y
            sum_xy = torch.sum(preds[i] * labels[i])  # xy
            sum_x2 = torch.sum(torch.pow(preds[i], 2))  # x^2
            sum_y2 = torch.sum(torch.pow(labels[i], 2))  # y^2
            N = preds.shape[1]
            pearson = (N * sum_xy - sum_x * sum_y) / (
                torch.sqrt((N * sum_x2 - torch.pow(sum_x, 2)) * (N * sum_y2 - torch.pow(sum_y, 2))))

            loss += 1 - pearson

        loss = loss / preds.shape[0]
        return loss


class AvgrageMeter(object):

  def __init__(self):
    self.reset()

  def reset(self):
    self.avg = 0
    self.sum = 0
    self.cnt = 0

  def update(self, val, n=1):
    self.sum += val * n
    self.cnt += n
    self.avg = self.sum / self.cnt


def get_hr(y, sr=30, min=30, max=180):
    p, q = welch(y, sr, nfft=1e5/sr, nperseg=np.min((len(y)-1, 256)))
    return p[(p>min/60)&(p<max/60)][np.argmax(q[(p>min/60)&(p<max/60)])]*60


def train_model_my(model, train_loader, criterion_Pearson,criterion_MAE, optimizer, device, sig_out_hr_batch,a):
    model.train()

    loss_rPPG_avg = AvgrageMeter()
    loss_fre_avg = AvgrageMeter()
    train_mae = AvgrageMeter()
    total_loss_avg = AvgrageMeter()
    # print('train_loader len is ',len(train_loader))

    for i, result in enumerate(train_loader):  # dataloader randomly samples a video clip with length T,imgs (2,3,300,128,128)
        # print('batch nuber is',i)
        if result is not None:
            train_imgs, train_bvp = result  # TODO: train_hr_avg 不知道是什么东西

            train_imgs = train_imgs.to(device)  # (B,3,300,128,128)
            train_hr_avg = torch.tensor([get_hr(i) for i in train_bvp]).float().to(device)
            train_bvp = train_bvp.to(device)

            # train_hr_avg = train_hr_avg.to(device)

            # fps = fps.to(device)
            hr_avg_norm = train_hr_avg - 40
            len_train = train_imgs.shape[0]
            # print('train batch size is ', len_train)

            # model forward propagation
            model_output = model(train_imgs)  # (2,5,300)
            # model_output_ulb = model(train_imgs_ulb)  # (2,5,300)

            rppg_lb = model_output[:, -1]  # get rppg,(2,300)

            # loss_rPPG = criterion_Pearson(rppg, bvp_norm)

            loss_rPPG_lb = criterion_Pearson(rppg_lb, train_bvp)

            train_hr_gt = sig_out_hr_batch(train_bvp.detach().cpu().numpy(), 0.6, 4, 30)  # [112. 106.]
            train_hr_pre = sig_out_hr_batch(rppg_lb.detach().cpu().numpy(), 0.6, 4, 30)  # array [112. 106.]
            # print("train_hr_gt:  ",train_hr_gt)  #TODO:(1,)??????
            # print("train_hr_pre:  ", train_hr_pre)
            train_hr_mae = criterion_MAE(train_hr_gt, train_hr_pre)
            # print('train_hr_mae is ',train_hr_mae) # around 30

            fre_loss = 0.0

            for bb in range(len_train):  # input.shape[0]=2,bb=0,1
                loss_distribution_kl, fre_loss_temp, _ = TorchLossComputer.cross_entropy_power_spectrum_DLDL_softmax2(
                    rppg_lb[bb], hr_avg_norm[bb], 30, std=1.0)  # std=1.1
                fre_loss = fre_loss + fre_loss_temp

            train_fre_loss = fre_loss / len_train  # batch_average
            a = a
            # total_loss = a * loss_rPPG_lb + b * fre_loss + c * loss_ul
            total_loss = a * loss_rPPG_lb + train_fre_loss

            # all losses have been averaged before, here is only one number, the average is itself
            loss_rPPG_avg.update(loss_rPPG_lb.data, len_train)  # n is batch size
            loss_fre_avg.update(train_fre_loss.data, len_train)
            total_loss_avg.update(total_loss, len_train)
            train_mae.update(train_hr_mae, len_train)

            # optimize
            optimizer.zero_grad()
            total_loss.backward()
            optimizer.step()

            if i % 20 == 0:
               print('batch=%d,loss_rPPG_avg= %.4f, fre_loss_avg= %.4f,total_loss_avg=%.4f,train_mae_avg=%.4f' %
                      (i, loss_rPPG_avg.avg, loss_fre_avg.avg, total_loss_avg.avg, train_mae.avg))


    return loss_rPPG_avg.avg, loss_fre_avg.avg, total_loss_avg.avg,train_mae.avg


def val_model_my(model, val_loader, criterion_Pearson, criterion_MAE, device, sig_out_hr_batch,a):

    model.eval()

    val_loss_rppg = AvgrageMeter()
    val_loss_fre = AvgrageMeter()
    val_total_loss = AvgrageMeter()
    val_mae = AvgrageMeter()

    flag = 1
    # with torch.no_grad():
    for val_result in val_loader:  # dataloader randomly samples a video clip with length T,imgs (2,3,300,128,128)
        # if flag!=1:
        #     flag +=1
        #     continue

        if val_result is not None:
            # val_img, val_bvp, val_hr_avg, val_fps = val_result  # train_imgs(2,3,300,128,128)
            val_img, val_bvp = val_result  # train_imgs(2,3,300,128,128)

            val_img = val_img.to(device)  # (B,3,300,128,128)
            # val_hr_avg = torch.tensor([get_hr(i) for i in val_bvp]).float()
            val_bvp = val_bvp.to(device)

            # val_hr_mean = val_hr_avg.to(device)
            # val_fps = val_fps.to(device)  # (B,) (2,)
            # val_hr_avg_norm = val_hr_mean - 40
            # len_val = val_img.shape[0]  # batch size

            val_rppg_list = []
            val_bvp_list = []
            val_fre = 0
            for i in range(230,242):
                b,g,r = [val_img[1].permute(1, 2, 3, 0).cpu().numpy()[i].astype(np.int32)[:,:,j] for j in range(3)]
                img_new1 = cv2.merge([r, g, b])

                plt.subplot(4, 3, i-230 + 1)
                # g = img_batch[i][:,:,1]
                # img = r*0+g+b*0
                plt.imshow(img_new1)
                plt.axis('off')
                # print(val_img[0].permute(1, 2, 3, 0).cpu().numpy()[i].astype(np.int8))
            plt.show()

            # val_img.requires_grad_()  #记录输入视频v
            val_img = Variable(val_img, requires_grad=True)
            val_model_output = model(val_img)  # (2,5,300) last is model_output shape is torch.Size([1, 5, 300])
            val_rppg_clip = val_model_output[:, -1]
            # val_rppg_list.append(val_rppg_clip.detach().cpu().numpy())  # predicted results
            # val_bvp_list.append(val_bvp.detach().cpu().numpy())  # gt result
            #
            # for cc in range(len_val):  # batch_size
            #     _, val_fre_loss_temp, _ = TorchLossComputer.cross_entropy_power_spectrum_DLDL_softmax2(
            #         val_rppg_clip[cc], val_hr_avg_norm[cc], 30, std=1.0)  # std=1.1
            #     val_fre = val_fre + val_fre_loss_temp
            #
            # val_fre_loss = val_fre / len_val
            val_loss_rPPG = criterion_Pearson(val_rppg_clip, val_bvp)

            # val_total_loss_sample = a * val_loss_rPPG + val_fre_loss

            # val_hr_gt = sig_out_hr_batch(val_bvp.detach().cpu().numpy(), 0.6, 4, 30)  # [112. 106.]
            # val_hr_pre = sig_out_hr_batch(val_rppg_clip.detach().cpu().numpy(), 0.6, 4, 30)  # array [112. 106.]

            # val_hr_mae = criterion_MAE(val_hr_gt, val_hr_pre)
            # val_loss_rppg.update(val_loss_rPPG, len_val)
            # val_loss_fre.update(val_fre_loss, len_val)
            # val_total_loss.update(val_total_loss_sample, len_val)
            # val_mae.update(val_hr_mae, len_val)
            val_loss_rPPG.requires_grad_(True)
            val_loss_rPPG.backward()
            saliency = abs(val_img.grad.data)
            # print(saliency.shape)
            img_batch = saliency[1].permute(1, 2, 3, 0).cpu().numpy()
            # print(img_batch.shape)

            for i in range(230,242):
                b,g,r = [img_batch[i][:,:,j] for j in range(3)]
                img_new1 = cv2.merge([r, g, b])
                g = g*1000000
                # 创建只包含绿色通道的图像
                green_image = cv2.merge([np.zeros_like(g), g, np.zeros_like(g)])
                plt.subplot(4, 3, i-230 + 1)
                # g = img_batch[i][:,:,1]
                # img = r*0+g+b*0
                plt.imshow(g)
                # plt.imshow(green_image)
                plt.axis('off')
                print(green_image)
            plt.show()
            # for i in range(255,260):
            #     plt.subplot(2, 5, i-255 + 1)
            #     # plt.imshow(img_batch[i]*10000000, cmap=plt.cm.hot)
            #     plt.imshow(img_batch[i]*100000, cmap=plt.cm.hot)
            #     plt.axis('off')
            #     print(img_batch[i])
            #     # plt.gcf().set_size_inches(12, 5)
            # plt.show()
        break

    # print('val_loss_rppg is %.4f, val_loss_fre is %.4f,val_total_loss is %.4f,val_mae_mean is %.4f' % (
    # val_loss_rppg.avg, val_loss_fre.avg, val_total_loss.avg, val_mae.avg))

    return val_loss_rppg.avg, val_loss_fre.avg, val_total_loss.avg, val_mae.avg

class PulseDataset(Dataset):
    """
    PURE, VIPL-hr, optospare and pff pulse dataset. Containing video frames and corresponding to them pulse signal.
    Frames are put in 4D tensor with size [c x d x w x h]
    """

    def __init__(self, train_list, T, length):
        """
        Initialize dataset
        :param train_list: list of sequences in dataset
        :param length: number of possible sequences
        :param T: length of generated sequence
        """
        self.frames_list = pd.DataFrame()
        for s in train_list:
            fr_list = list(np.load(s)['frame'])
            label_path = s.replace('PURE_filter_crop_numpy_box','PURE_filter_meta_numpy')
            label_path = label_path.replace('_test','')
            reference = np.load(label_path)["wave"]  # 原版
            # reference = np.load(label_path,allow_pickle=True)['wave'][0]   # 插值后版本

            ref_resample = resample(reference, len(fr_list))
            ref_resample = (ref_resample - np.mean(ref_resample)) / (np.max(ref_resample)-np.min(ref_resample))

            self.frames_list = self.frames_list.append(pd.DataFrame({'frames': fr_list, 'labels': ref_resample}))

        self.length = length
        self.T = T
        print('Found', self.__len__(), "sequences")

    def __len__(self):
        return self.length

    def __getitem__(self, idx):
        if torch.is_tensor(idx):
            idx = idx.tolist()
        frames = []
        # frames = self.frames_list.iloc[idx: idx+self.T, 0].values.astype(np.float32)
        # frames = torch.from_numpy(frames)

        for fr in range(idx, idx+self.T):  # frames from idx to idx+seq_len
            image = torch.tensor(self.frames_list.iloc[fr, 0].astype(np.float32))
            frames.append(image)
            # img_name = os.path.join(self.frames_list.iloc[fr, 0])  # path to image
            # image = Image.open(img_name)
            # image = image.resize((self.img_width, self.img_height))
            #
            # if self.transform:
            #     image = self.transform(image)
            # frames.append(image)

        frames = torch.stack(frames)
        frames = frames.permute(3,0,1,2)
        frames = torch.squeeze(frames, dim=1)
        # frames = (frames-torch.mean(frames))/torch.std(frames)*255
        lab = np.array(self.frames_list.iloc[idx:idx + self.T, 1])
        labels = torch.tensor(lab, dtype=torch.float)

        sample = (frames, labels)
        return sample

def my_collate_fn(batch):
    '''
    batch 实际上是一个列表，列表的长度就是一个batch_size，列表的每一个元素形如(data, label)，
          这实际上是定义DataSet的时候，每一个__getitem__得到的元素
          batch :是一个列表，列表的长度是 batch_size
               列表的每一个元素是 (x,y) 这样的元组tuple，元祖的两个元素分别是x,y
               大致的格式如下 [(x1,y1),(x2,y2),(x3,y3)...(xn,yn)]

    '''
    # 过滤为None的数据

    batch = list(filter(lambda x: x is not None, batch))

    if batch==[]:
        return None

    # if len(batch)==0:
    #     print('batch is',batch)
    #     print('batch type is', type(batch))

    return default_collate(batch)  # 用默认方式拼接过滤后的batch数据，这里的default_collate就是pytorch默认给collate_fn传递的函数，需要导入才能使用

def PURE_LU_split():
    # split PURE dataset into training and testing parts
    # the function returns the file paths for the training set and test set.
    # TODO: if you want to train on another dataset, you should define new train-test split function.

    npy_dir = '/data/xieyiping/dataset/PURE_numpy/PURE_filter_crop_numpy_box'
    train_list = []
    val_list = []

    val_subject = [2, 3, 10]

    for subject in range(1, 11):
        for i in range(1, 7):
            # print(h5_dir + '/{:0>2d}-{:0>2d}.npz'.format( subject, i))
            if os.path.isfile(npy_dir + '/{:0>2d}-{:0>2d}.npz'.format(subject, i)):
                if subject in val_subject:
                    val_list.append(npy_dir + '/{:0>2d}-{:0>2d}.npz'.format(subject, i))
                else:
                    train_list.append(npy_dir + '/{:0>2d}-{:0>2d}.npz'.format(subject, i))

    return train_list, val_list