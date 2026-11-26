"""DeepLabV3+ architecture used by the inference worker."""
import torch
from torch import nn


class ConvBNReLU(nn.Sequential):
    def __init__(self, in_channels: int, out_channels: int, kernel_size: int = 3,
                 stride: int = 1, dilation: int = 1):
        super().__init__(
            nn.Conv2d(in_channels, out_channels, kernel_size, stride,
                      padding=(kernel_size // 2) * dilation, dilation=dilation, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
        )


class SeparableConv(nn.Sequential):
    def __init__(self, in_channels: int, out_channels: int, stride: int = 1,
                 dilation: int = 1):
        super().__init__(
            nn.Conv2d(in_channels, in_channels, 3, stride, dilation,
                      dilation=dilation, groups=in_channels, bias=False),
            nn.BatchNorm2d(in_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(in_channels, out_channels, 1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
        )


class ASPP(nn.Module):
    """Atrous spatial pyramid pooling at output stride 16."""

    def __init__(self, in_channels: int, channels: int, rates: tuple[int, ...]):
        super().__init__()
        self.branches = nn.ModuleList([
            ConvBNReLU(in_channels, channels, kernel_size=1),
            *(SeparableConv(in_channels, channels, dilation=rate) for rate in rates),
        ])
        self.image_pool = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Conv2d(in_channels, channels, 1, bias=False),
            nn.ReLU(inplace=True),
        )
        self.project = ConvBNReLU(channels * (len(self.branches) + 1), channels * 2,
                                  kernel_size=1)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        pooled = nn.functional.interpolate(self.image_pool(value), size=value.shape[-2:],
                                           mode="bilinear", align_corners=False)
        return self.project(torch.cat([*(branch(value) for branch in self.branches), pooled], dim=1))


class DeepLabV3Plus(nn.Module):
    """Lightweight DeepLabV3+ with stride-16 encoder and stride-4 decoder skip."""

    def __init__(self, width: int = 32, rates: tuple[int, ...] = (2, 4, 6)):
        super().__init__()
        self.stem = ConvBNReLU(3, width, stride=2)
        self.low = ConvBNReLU(width, width * 2, stride=2)
        self.encoder8 = nn.Sequential(
            SeparableConv(width * 2, width * 3, stride=2),
            SeparableConv(width * 3, width * 3),
        )
        self.encoder16 = nn.Sequential(
            SeparableConv(width * 3, width * 5, stride=2),
            SeparableConv(width * 5, width * 5),
            SeparableConv(width * 5, width * 5),
        )
        self.aspp = ASPP(width * 5, width * 2, rates)
        self.low_project = ConvBNReLU(width * 2, width, kernel_size=1)
        self.decoder = nn.Sequential(
            SeparableConv(width * 5, width * 2),
            SeparableConv(width * 2, width * 2),
            nn.Conv2d(width * 2, 1, 1),
        )

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        size = image.shape[-2:]
        low = self.low(self.stem(image))
        high = self.encoder16(self.encoder8(low))
        high = nn.functional.interpolate(self.aspp(high), size=low.shape[-2:],
                                         mode="bilinear", align_corners=False)
        logits = self.decoder(torch.cat((high, self.low_project(low)), dim=1))
        return nn.functional.interpolate(logits, size=size, mode="bilinear", align_corners=False)
