import sys
import os

curPath = os.path.abspath(os.path.dirname(__file__))
rootPath = os.path.split(curPath)[0]
sys.path.append(rootPath)

import torch
import math
import time
import json
import logging
from torchmeta.utils.data import BatchMetaDataLoader

from learningTolearn.dataset import get_benchmark_by_name
from learningTolearn.method.optimization import ModelAgnosticMetaLearning, MetaSGD


def main(args):
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    device = torch.device('cuda:0' if args.use_cuda and torch.cuda.is_available() else 'cpu')

    if (args.output_folder is not None):  # args:'output_folder' 参数非空
        # 存放结果的文件夹不存在
        if not os.path.exists(args.output_folder):
            os.makedirs(args.output_folder)
            logging.debug('Creating folder `{0}`'.format(args.output_folder))

        # 存放结果的文件夹存在
        folder = os.path.join(args.output_folder, time.strftime('%Y-%m-%d_%H%M%S'))
        os.makedirs(folder)
        logging.debug('Creating folder `{0}`'.format(folder))

        args.folder = os.path.abspath(args.folder)
        args.model_path = os.path.abspath(os.path.join(folder, 'model.th'))

        # Save the configuration in a config.json file
        with open(os.path.join(folder, 'config.json'), 'w') as f:
            json.dump(vars(args), f, indent=2)
        logging.info('Saving configuration file in `{0}`'
                     .format(os.path.abspath(os.path.join(folder, 'config.json'))))

    # 加载数据集
    benchmark = get_benchmark_by_name(args.dataset,
                                      args.folder,
                                      args.num_ways,
                                      args.num_shots,
                                      args.num_shots_test,
                                      args.backbone,
                                      hidden_size=args.hidden_size)
    # 训练集
    meta_train_dataloader = BatchMetaDataLoader(benchmark.meta_train_dataset,
                                                batch_size=args.batch_size,
                                                shuffle=True,
                                                num_workers=args.num_workers,
                                                pin_memory=True)
    # 验证集
    meta_val_dataloader = BatchMetaDataLoader(benchmark.meta_val_dataset,
                                              batch_size=args.batch_size,
                                              shuffle=True,
                                              num_workers=args.num_workers,
                                              pin_memory=True)
    # 优化器
    meta_optimizer = torch.optim.Adam(benchmark.model.parameters(), lr=args.meta_lr)
    # 模型
    # metalearner = ModelAgnosticMetaLearning(benchmark.model,
    #                                         meta_optimizer,
    #                                         first_order=args.first_order,
    #                                         num_adaptation_steps=args.num_steps,
    #                                         step_size=args.step_size,
    #                                         loss_function=benchmark.loss_function,
    #                                         device=device)
    metalearner = MetaSGD(benchmark.model,
                          meta_optimizer,
                          # scheduler=torch.optim.lr_scheduler.StepLR(meta_optimizer, step_size=30),
                          device=device)
    # Score
    best_value = None

    # Training loop
    epoch_desc = 'Epoch {{0: <{0}d}}'.format(1 + int(math.log10(args.num_epochs)))
    for epoch in range(args.num_epochs):
        metalearner.train(meta_train_dataloader,
                          max_batches=args.num_batches,
                          verbose=args.verbose,
                          desc='Training',
                          leave=False)
        results = metalearner.evaluate(meta_val_dataloader,
                                       max_batches=args.num_batches,
                                       verbose=args.verbose,
                                       desc=epoch_desc.format(epoch + 1))

        # Save best model
        if (best_value is None) or (('accuracies_after' in results) and (best_value < results['accuracies_after'])):
            best_value = results['accuracies_after']
            save_model = True
        elif (best_value is None) or (best_value > results['mean_outer_loss']):
            best_value = results['mean_outer_loss']
            save_model = True
        else:
            save_model = False

        if save_model and (args.output_folder is not None):
            with open(args.model_path, 'wb') as f:
                torch.save(benchmark.model.state_dict(), f)

    # if hasattr(meta_train_dataset, 'close'):
    #     meta_train_dataset.close()
    #     meta_val_dataset.close()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser('MAML')

    # General
    parser.add_argument('folder', type=str, default='/few-shot-datasets',
                        help='Path to the folder the data is downloaded to.')
    parser.add_argument('--dataset', type=str,
                        choices=['sinusoid', 'omniglot', 'miniimagenet', 'tieredimagenet'], default='omniglot',
                        help='Name of the dataset (default: omniglot).')
    parser.add_argument('--output-folder', type=str, default='/result',
                        help='Path to the output folder to save the model.')
    parser.add_argument('--num-ways', type=int, default=5,
                        help='Number of classes per task (N in "N-way", default: 5).')
    parser.add_argument('--num-shots', type=int, default=5,
                        help='Number of training example per class (k in "k-shot", default: 5).')
    parser.add_argument('--num-shots-test', type=int, default=15,
                        help='Number of test example per class. '
                             'If negative, same as the number of training examples `--num-shots` (default: 15).')

    # Model
    parser.add_argument('--hidden-size', type=int, default=64,
                        help='Number of channels in each convolution layer of the VGG network '
                             '(default: 64).')
    parser.add_argument('--backbone', type=str, default='resnet10',
                        help='The backbone of the few-shot training set. (default: resnet10)')

    # Optimization
    parser.add_argument('--batch-size', type=int, default=25,
                        help='Number of tasks in a batch of tasks (default: 25).')
    parser.add_argument('--num-steps', type=int, default=1,
                        help='Number of fast adaptation steps, ie. gradient descent '
                             'updates (default: 1).')
    parser.add_argument('--num-epochs', type=int, default=50,
                        help='Number of epochs of meta-training (default: 50).')
    parser.add_argument('--num-batches', type=int, default=100,
                        help='Number of batch of tasks per epoch (default: 100).')
    parser.add_argument('--step-size', type=float, default=0.1,
                        help='Size of the fast adaptation step, ie. learning rate in the '
                             'gradient descent update (default: 0.1).')
    parser.add_argument('--first-order', action='store_true',
                        help='Use the first order approximation, do not use higher-order '
                             'derivatives during meta-optimization.')
    parser.add_argument('--meta-lr', type=float, default=0.001,
                        help='Learning rate for the meta-optimizer (optimization of the outer '
                             'loss). The default optimizer is Adam (default: 1e-3).')

    # Misc
    parser.add_argument('--num-workers', type=int, default=1,
                        help='Number of workers to use for data-loading (default: 1).')
    parser.add_argument('--verbose', action='store_true', default=True)
    parser.add_argument('--use-cuda', action='store_true', default=True)

    args = parser.parse_args()

    if args.num_shots_test <= 0:
        args.num_shots_test = args.num_shots

    main(args)
