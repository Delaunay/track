from track import TrackClient

import sys
import torch
import torch.nn as nn
import torch.optim
import torch.utils.data
import torchvision.transforms as transforms
import torchvision.datasets as datasets
import torchvision.models as models
import torch.nn.functional as F
import argparse

sys.stderr = sys.stdout

DISABLED = True


def test_end_to_end():
    if DISABLED:
        return

    import mlflow

    parser = argparse.ArgumentParser(description='Convnet training for torchvision models')

    parser.add_argument('--batch-size', '-b', type=int, help='batch size', default=32)
    parser.add_argument('--cuda', action='store_true', dest='cuda', default=True, help='enable cuda')
    parser.add_argument('--no-cuda', action='store_false', dest='cuda', help='disable cuda')

    parser.add_argument('--workers', '-j', type=int, default=4, help='number of workers/processors to use')
    parser.add_argument('--seed', '-s', type=int, default=0, help='seed to use')
    parser.add_argument('--epochs', '-e', type=int, default=2, help='number of epochs')

    parser.add_argument('--arch', '-a', metavar='ARCH', default='convnet')
    parser.add_argument('--lr', '--learning-rate', default=0.1, type=float, metavar='LR')
    parser.add_argument('--momentum', default=0.9, type=float, metavar='MT')
    parser.add_argument('--opt-level', default='O0', type=str)

    parser.add_argument('--data', metavar='DIR', default='mnist', help='path to dataset')

    # ----

    from mlflow.tracking import set_tracking_uri

    set_tracking_uri('file://trash')
    exp_id = mlflow.set_experiment('lulz')
    args = parser.parse_args([])
    device = torch.device('cpu')

    try:
        import torch.backends.cudnn as cudnn
        cudnn.benchmark = True
    except:
        pass

    class ConvClassifier(nn.Module):
        def __init__(self, input_shape=(1, 28, 28)):
            super(ConvClassifier, self).__init__()

            c, h, w = input_shape

            self.convs = nn.Sequential(
                nn.Conv2d(c, 10, kernel_size=5),
                nn.MaxPool2d(2),
                nn.ReLU(True),
                nn.Conv2d(10, 20, kernel_size=5),
                nn.Dropout2d(),
                nn.MaxPool2d(2)
            )

            _, c, h, w = self.convs(torch.rand(1, *input_shape)).shape
            self.conv_output_size = c * h * w

            self.fc1 = nn.Linear(self.conv_output_size, self.conv_output_size // 4)
            self.fc2 = nn.Linear(self.conv_output_size // 4, 10)

        def forward(self, x):
            x = self.convs(x)
            x = x.view(-1, self.conv_output_size)
            x = F.relu(self.fc1(x))
            x = F.dropout(x, training=self.training)
            x = self.fc2(x)
            return F.log_softmax(x, dim=1)

    # ----
    if args.arch == 'convnet':
        model = ConvClassifier(input_shape=(1, 28, 28))
    else:
        model = models.__dict__[args.arch]()

    model = model.to(device)

    criterion = nn.CrossEntropyLoss().to(device)

    optimizer = torch.optim.SGD(
        model.parameters(),
        args.lr,
        args.momentum
    )

    dataset_ctor = datasets.ImageFolder
    kwargs = {
        'transform': transforms.Compose([
            transforms.RandomResizedCrop(28),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
        ])
    }
    if args.data == 'mnist':
        dataset_ctor = datasets.mnist.MNIST
        args.data = '/tmp'
        kwargs['download'] = True
        kwargs['train'] = True
        args.workers = 1

    train_dataset = dataset_ctor(args.data, **kwargs)

    # ----
    train_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=args.workers,
        pin_memory=True
    )

    def next_batch(batch_iter):
        try:
            input, target = next(batch_iter)
            input = input.to(device)
            target = target.to(device)
            return input, target

        except StopIteration:
            return None

    with mlflow.start_run() as run:
        run.log_params(vars(args))
        run.set_tag('key', 'value')

        model.train()
        for epoch in range(args.epochs):
            batch_iter = iter(train_loader)

            batch_id = 0
            epoch_loss = 0
            while True:
                batch = next_batch(batch_iter)

                if batch is None:
                    break

                input, target = batch

                output = model(input)
                loss = criterion(output, target)

                epoch_loss += loss.item()
                run.log_metrics(step=(epoch, batch_id), epoch_loss=loss.item())
                # trial.log_metrics(step=(epoch, batch_id), loss=loss.item())

                # compute gradient and do SGD step
                optimizer.zero_grad()

                # with amp.scale_loss(loss, optimizer) as scaled_loss:
                #    scaled_loss.backward()
                loss.backward()

                optimizer.step()
                batch_id += 1

                # ---
            epoch_loss /= len(train_loader)
            run.log_metrics(step=epoch, epoch_loss=epoch_loss)
                # ---
    mlflow.end_run(status='FINISHED')


if __name__ == '__main__':
    test_end_to_end()