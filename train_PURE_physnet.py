from PhysNetModel import PhysNet

from torch import optim
from torch.utils.data import DataLoader
import wandb
from sklearn.metrics import mean_absolute_error

import warnings

from mypulse_sampler import PulseSampler
from sacred import Experiment
from sacred.observers import FileStorageObserver
import pandas as pd
from scipy.signal import welch
# from NegativeMaxCrossCorr import NegativeMaxCrossCorr
from train_PURE_utils import *

# os.environ['CUDA_VISIBLE_DEVICES'] = '0,1,2'
ex = Experiment('model_train', save_git_info=False)
warnings.filterwarnings('ignore')

# def set_seed(seed):
#     torch.manual_seed(seed)
#     np.random.seed(seed)
#     # os.environ['PYTHONHASHSEED'] = str(seed)
#     if torch.cuda.is_available():
#         torch.cuda.manual_seed_all(seed)
#         torch.backends.cudnn.deterministic = True
#         torch.backends.cudnn.benchmark = False


if torch.cuda.is_available():
    # device = torch.device('cuda')
    device = torch.device('cuda')
    torch.backends.cudnn.enabled = True
    torch.backends.cudnn.benchmark = True
else:
    device = torch.device('cpu')

@ex.config
def my_config():
    # here are some hyperparameters in our method
    _run = 3
    # hyperparams for model training
    total_epoch = 60  # total number of epochs for training the model
    lr = 1e-4  # learning rate
    # a = 0.1
    a = 1
    # ratio = 0.5

    batch_size = 2
    in_ch = 3  # TODO: number of input video channels, in_ch=3 for RGB videos, in_ch=1 for NIR videos.

    # hyperparams for ST-rPPG block
    fs = 30  # video frame rate, TODO: modify it if your video frame rate is not 30 fps.
    T = 300  # temporal dimension of ST-rPPG block, default is 10 seconds.
    S = 2  # spatial dimenion of ST-rPPG block, default is 2x2.

    result_dir = '/data/xieyiping/projectone/Physnet/Physnet_randloader/result_PURE'  # store checkpoints and training recording
    ex.observers.append(FileStorageObserver(result_dir))

@ex.automain
def my_main(_run, total_epoch, lr, a, batch_size, fs, T, S, in_ch, result_dir):

    exp_dir = result_dir + '/%d' % (int(_run._id))  # store experiment recording to the path
    # get the training and test file path list by spliting the dataset
    train_list, test_list = PURE_LU_split()  # TODO: you should define your function to split your dataset for training and testing
    np.save(exp_dir + '/train_list.npy', train_list)
    np.save(exp_dir + '/test_list.npy', test_list)

    # define the dataloader,use my define dataset
    end_indexes = []
    for s in train_list:
        end_indexes.append(len(np.load(s)['frame']))
    end_indexes = [0, *end_indexes]
    # print(end_indexes)
    # define the dataloader
    sampler = PulseSampler(end_indexes, T, False)
    train_dataset_all = PulseDataset(train_list, T, length=len(sampler))
    print('train_dataset len is', len(train_dataset_all))  # 0
    # end_indexes_test = []
    # for s in test_list:
    #     end_indexes_test.append(len(np.load(s)['frame']))
    # end_indexes_test = [0, *end_indexes_test]
    # # print(end_indexes_test)
    # sampler = PulseSampler(end_indexes_test, T, False)
    # test_dataset = PulseDataset(test_list, T,
    #                        length=len(sampler))
    # print('test_dataset len is', len(test_dataset))  # 0


    train_loader_all = DataLoader(train_dataset_all, batch_size=batch_size,  # two videos for contrastive learning
                                  shuffle=True, num_workers=1, pin_memory=True, drop_last=True,
                                  collate_fn=my_collate_fn)  # 953, 953*2=1906
    # test_loader = DataLoader(test_dataset, batch_size=batch_size,  # two videos for contrastive learning
    #                         shuffle=True, num_workers=1, pin_memory=True, drop_last=True, collate_fn=my_collate_fn)


    model=PhysNet(S, in_ch=3)

    # model.load_state_dict(torch.load('/data1/vsign/VIPL_contrast-phys-master_results_supervise_alignedBVP/7/epoch20.pt'))

    model.to(device)

    criterion_Pearson = Neg_Pearson()
    criterion_MAE = mean_absolute_error

    opt=optim.AdamW(model.parameters(), lr=lr)

    best_test_mae = 10000
    best_epoch = 0

    for e in range(total_epoch):


        torch.cuda.empty_cache()

        train_loss_rPPG, train_loss_fre, train_total_loss, train_mae = train_model_my(model, train_loader_all,
                                                                                      criterion_Pearson, criterion_MAE,
                                                                                      opt, device,
                                                                                      sig_out_hr_batch,
                                                                                      a)
        # torch.save(model.state_dict(), exp_dir + '/epoch%d.pt' % e)
        # print('save to %s', exp_dir + '/epoch%d.pt' % e)

        # val_loss_rppg, val_loss_fre, val_total_loss, val_mae = val_model_my(model, val_loader,
        #                                                                     criterion_Pearson, criterion_MAE, device,
        #                                                                     sig_out_hr_batch,
        #                                                                     a)

        # test_mae, test_std, test_RMSE, test_r = test_model_batch(model, test_loader, criterion_MAE, device, sig_out_hr_batch)


        if train_mae < best_test_mae:
            best_test_mae = train_mae
            best_epoch = e
        torch.save(model.state_dict(), exp_dir + '/epoch%d.pt' % e)
    print('best_val_mae is %f and best epoch is %f' % (best_test_mae, best_epoch))









