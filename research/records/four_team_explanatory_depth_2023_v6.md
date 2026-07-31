# 2023 四队逐站可解释深度报告（v6）

- 性质：`retrospective_explanatory_extension_after_2025_review`；共 22 站。
- Layer A：traffic 覆盖 94.7%；特征只按 2024 时间外选择，2025 仅锁定报告。
- 方向约定：理论上限 advantage_z / 90s ms 越大越快；CI 是统计不确定性，不是物理极限范围。

## 分站总览

| 分站 | R 圈 | traffic覆盖 | dirty-air | 方差比 | 成对置信度 | 门控 | 等级 | 理论上限第一 | 90s ms | 模型↔配速 ρ | 配速↔赛果proxy ρ |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | ---: |
| Abu Dhabi Grand Prix | 409 | 88.5% | 44.5% | 0.190 | 0.754 | pass | low | McLaren | 645.9 | 0.857 | 0.881 |
| Australian Grand Prix | 267 | 84.6% | 43.8% | 0.417 | 0.831 | pass | medium | Red Bull Racing | 1860.8 | 0.964 | 0.786 |
| Austrian Grand Prix | 460 | 88.3% | 33.9% | 0.758 | 0.892 | pass | high | McLaren | 1028.2 | 1.000 | 0.976 |
| Azerbaijan Grand Prix | 346 | 87.3% | 58.7% | 1.967 | 0.942 | pass | high | Mercedes | 202.4 | 0.976 | 0.857 |
| Bahrain Grand Prix | 321 | 85.0% | 31.5% | 0.355 | 0.853 | pass | medium | Mercedes | 758.6 | 0.857 | 0.690 |
| Belgian Grand Prix | 228 | 85.1% | 27.2% | 0.205 | 0.785 | pass | low | Red Bull Racing | 456.8 | 0.786 | 0.964 |
| British Grand Prix | 338 | 87.0% | 45.0% | 0.160 | 0.744 | pass | low | McLaren | 781.7 | 0.643 | 0.667 |
| Canadian Grand Prix | 437 | 87.0% | 43.5% | 0.419 | 0.794 | pass | medium | McLaren | 1262.5 | 0.857 | 0.976 |
| Dutch Grand Prix | 302 | 87.1% | 56.0% | 0.087 | 0.782 | pass | low | Red Bull Racing | 715.4 | 0.833 | 0.881 |
| Hungarian Grand Prix | 502 | 87.3% | 30.7% | 0.137 | 0.802 | pass | low | Red Bull Racing | 518.9 | 1.000 | 0.833 |
| Italian Grand Prix | 370 | 88.1% | 58.9% | 0.110 | 0.761 | pass | low | Ferrari | 427.2 | 0.810 | 0.643 |
| Japanese Grand Prix | 302 | 86.4% | 28.5% | 0.199 | 0.720 | pass | low | Red Bull Racing | 807.6 | 0.571 | 0.810 |
| Las Vegas Grand Prix | 238 | 87.8% | 68.5% | 0.052 | 0.685 | pass | low | Red Bull Racing | 1239.1 | 0.643 | 0.571 |
| Mexico City Grand Prix | 416 | 86.1% | 42.3% | 0.191 | 0.794 | pass | low | Mercedes | 962.7 | 0.893 | 0.821 |
| Miami Grand Prix | 430 | 88.1% | 38.4% | 1.922 | 0.905 | pass | high | Red Bull Racing | 1245.9 | 0.952 | 0.976 |
| Monaco Grand Prix | 366 | 87.4% | 57.9% | 0.812 | 0.757 | pass | medium | McLaren | 296.3 | 0.738 | 0.738 |
| Qatar Grand Prix | 256 | 82.4% | 32.0% | 0.240 | 0.632 | pass | low | Red Bull Racing | 747.8 | 0.543 | 0.771 |
| Saudi Arabian Grand Prix | 347 | 88.5% | 41.8% | 1.912 | 0.917 | pass | high | Ferrari | 547.3 | 0.786 | 0.905 |
| Singapore Grand Prix | 375 | 86.9% | 70.4% | 0.285 | 0.715 | pass | medium | Ferrari | 853.5 | 0.786 | 0.476 |
| Spanish Grand Prix | 481 | 87.9% | 28.1% | 0.389 | 0.860 | pass | medium | Red Bull Racing | 1177.4 | 0.976 | 0.952 |
| São Paulo Grand Prix | 425 | 85.4% | 36.9% | 0.128 | 0.774 | pass | low | McLaren | 546.0 | 0.893 | 0.964 |
| United States Grand Prix | 355 | 87.6% | 36.1% | 0.091 | 0.745 | pass | low | Red Bull Racing | 991.5 | 0.548 | 0.905 |

## 三种排序（从快到慢）

| 分站 | 模型车手组合序 | 观测干净配速序 | classification proxy 序 |
| --- | --- | --- | --- |
| Abu Dhabi Grand Prix | VER > PER > NOR > HAM > RUS > LEC > PIA > SAI | PER > VER > RUS > NOR > LEC > HAM > PIA > SAI | VER > PER > LEC > RUS > NOR > PIA > HAM > SAI |
| Australian Grand Prix | VER > SAI > PER > HAM > NOR > RUS > PIA | SAI > VER > PER > HAM > NOR > RUS > PIA | VER > HAM > SAI > PER > NOR > PIA > RUS |
| Austrian Grand Prix | VER > PER > LEC > SAI > NOR > HAM > RUS > PIA | VER > PER > LEC > SAI > NOR > HAM > RUS > PIA | VER > LEC > PER > SAI > NOR > HAM > RUS > PIA |
| Azerbaijan Grand Prix | VER > PER > HAM > RUS > LEC > SAI > NOR > PIA | VER > PER > HAM > LEC > RUS > SAI > NOR > PIA | PER > VER > LEC > SAI > HAM > RUS > NOR > PIA |
| Bahrain Grand Prix | VER > PER > LEC > HAM > RUS > SAI > NOR > PIA | VER > PER > LEC > NOR > HAM > RUS > SAI > PIA | VER > PER > LEC > SAI > HAM > RUS > PIA > NOR |
| Belgian Grand Prix | VER > PER > HAM > RUS > LEC > NOR > SAI | VER > LEC > PER > HAM > RUS > NOR > SAI | VER > PER > LEC > HAM > RUS > NOR > SAI |
| British Grand Prix | VER > NOR > HAM > RUS > PER > PIA > LEC > SAI | RUS > VER > PIA > NOR > HAM > PER > SAI > LEC | VER > NOR > HAM > PIA > RUS > PER > LEC > SAI |
| Canadian Grand Prix | VER > LEC > PER > HAM > SAI > RUS > NOR > PIA | VER > HAM > LEC > SAI > PER > NOR > RUS > PIA | VER > HAM > LEC > SAI > PER > RUS > NOR > PIA |
| Dutch Grand Prix | VER > PER > NOR > HAM > RUS > PIA > LEC > SAI | VER > PER > NOR > HAM > SAI > PIA > RUS > LEC | VER > PER > SAI > HAM > NOR > PIA > LEC > RUS |
| Hungarian Grand Prix | VER > PER > HAM > NOR > RUS > LEC > PIA > SAI | VER > PER > HAM > NOR > RUS > LEC > PIA > SAI | VER > NOR > PER > HAM > PIA > LEC > RUS > SAI |
| Italian Grand Prix | VER > PER > LEC > SAI > NOR > HAM > RUS > PIA | LEC > PER > VER > SAI > NOR > PIA > RUS > HAM | VER > PER > SAI > LEC > RUS > HAM > NOR > PIA |
| Japanese Grand Prix | VER > PER > NOR > LEC > PIA > SAI > HAM > RUS | VER > NOR > SAI > LEC > PIA > HAM > PER > RUS | VER > NOR > PIA > LEC > HAM > SAI > RUS > PER |
| Las Vegas Grand Prix | VER > PER > LEC > HAM > RUS > SAI > PIA | LEC > PER > VER > PIA > HAM > RUS > SAI | VER > LEC > PER > RUS > SAI > HAM > PIA |
| Mexico City Grand Prix | VER > HAM > RUS > NOR > LEC > SAI > PIA | VER > HAM > NOR > LEC > RUS > SAI > PIA | VER > HAM > LEC > SAI > NOR > RUS > PIA |
| Miami Grand Prix | VER > PER > HAM > RUS > LEC > SAI > NOR > PIA | VER > PER > RUS > HAM > SAI > LEC > NOR > PIA | VER > PER > RUS > SAI > HAM > LEC > NOR > PIA |
| Monaco Grand Prix | VER > PER > LEC > HAM > SAI > RUS > NOR > PIA | VER > LEC > SAI > HAM > RUS > PER > NOR > PIA | VER > HAM > RUS > LEC > SAI > NOR > PIA > PER |
| Qatar Grand Prix | NOR > VER > PER > PIA > RUS > LEC | NOR > PIA > VER > RUS > LEC > PER | VER > PIA > NOR > RUS > LEC > PER |
| Saudi Arabian Grand Prix | VER > PER > HAM > LEC > RUS > SAI > NOR > PIA | PER > VER > SAI > HAM > RUS > LEC > PIA > NOR | PER > VER > RUS > HAM > SAI > LEC > PIA > NOR |
| Singapore Grand Prix | HAM > RUS > VER > NOR > LEC > PER > SAI > PIA | HAM > NOR > RUS > LEC > VER > PIA > SAI > PER | SAI > NOR > HAM > RUS > LEC > VER > PIA > PER |
| Spanish Grand Prix | VER > HAM > PER > RUS > LEC > SAI > NOR > PIA | VER > HAM > PER > RUS > SAI > LEC > NOR > PIA | VER > HAM > RUS > PER > SAI > LEC > PIA > NOR |
| São Paulo Grand Prix | VER > PER > NOR > SAI > PIA > HAM > RUS | VER > PER > NOR > SAI > HAM > RUS > PIA | VER > NOR > PER > SAI > HAM > RUS > PIA |
| United States Grand Prix | VER > HAM > PER > RUS > LEC > NOR > SAI > PIA | VER > HAM > SAI > NOR > PER > RUS > PIA > LEC | VER > HAM > NOR > SAI > PER > LEC > RUS > PIA |

## 每队理论上限

| 分站 | 车队 | 融合 advantage_z p50 | CI80 | Q p50 | R p50 | 执行校正 R p50 |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| Abu Dhabi Grand Prix | McLaren | 0.720 | [-0.088, 1.484] | 1.049 | 0.147 | 0.160 |
| Abu Dhabi Grand Prix | Red Bull Racing | -0.134 | [-0.770, 0.631] | -0.407 | 0.389 | 0.166 |
| Abu Dhabi Grand Prix | Ferrari | -0.351 | [-0.912, 0.273] | -0.417 | -0.236 | 0.070 |
| Abu Dhabi Grand Prix | Mercedes | 0.346 | [-0.440, 1.086] | 0.469 | 0.157 | 0.068 |
| Australian Grand Prix | McLaren | -0.620 | [-4.129, 0.812] | -0.880 | -0.112 | 0.257 |
| Australian Grand Prix | Red Bull Racing | 2.089 | [0.797, 3.579] | 2.970 | 0.466 | 0.247 |
| Australian Grand Prix | Ferrari | -0.063 | [-2.391, 1.243] | -0.305 | 0.387 | 0.049 |
| Australian Grand Prix | Mercedes | -0.117 | [-1.274, 1.560] | -0.086 | -0.188 | 0.069 |
| Austrian Grand Prix | McLaren | 1.149 | [-0.276, 4.636] | 1.829 | -0.120 | 0.172 |
| Austrian Grand Prix | Red Bull Racing | 0.230 | [-11.281, 1.852] | -0.033 | 0.674 | 0.278 |
| Austrian Grand Prix | Ferrari | -0.901 | [-2.015, 2.982] | -1.640 | 0.534 | 0.300 |
| Austrian Grand Prix | Mercedes | 0.954 | [-0.308, 4.599] | 1.525 | -0.184 | 0.226 |
| Azerbaijan Grand Prix | McLaren | -0.007 | [-0.780, 0.897] | 0.339 | -0.629 | 0.061 |
| Azerbaijan Grand Prix | Red Bull Racing | 0.221 | [-0.809, 1.061] | 0.068 | 0.494 | 0.099 |
| Azerbaijan Grand Prix | Ferrari | 0.201 | [-0.995, 1.234] | 0.276 | 0.060 | 0.091 |
| Azerbaijan Grand Prix | Mercedes | 0.225 | [-1.553, 1.435] | 0.150 | 0.382 | 0.066 |
| Bahrain Grand Prix | McLaren | 0.103 | [-0.744, 0.919] | 0.479 | -0.580 | 0.149 |
| Bahrain Grand Prix | Red Bull Racing | 0.062 | [-0.892, 1.140] | -0.208 | 0.641 | 0.148 |
| Bahrain Grand Prix | Ferrari | -0.520 | [-1.519, 0.552] | -0.855 | 0.106 | 0.149 |
| Bahrain Grand Prix | Mercedes | 0.846 | [-0.274, 2.005] | 1.045 | 0.467 | 0.228 |
| Belgian Grand Prix | McLaren | 0.013 | [-0.623, 0.843] | 0.134 | -0.161 | 0.098 |
| Belgian Grand Prix | Red Bull Racing | 0.509 | [-0.148, 1.195] | 0.294 | 0.920 | 0.300 |
| Belgian Grand Prix | Ferrari | -0.254 | [-0.849, 0.448] | -0.074 | -0.595 | 0.083 |
| Belgian Grand Prix | Mercedes | 0.392 | [-0.437, 1.038] | 0.373 | 0.512 | 0.300 |
| British Grand Prix | McLaren | 0.872 | [-0.108, 1.926] | 1.223 | 0.216 | 0.226 |
| British Grand Prix | Red Bull Racing | -0.604 | [-2.621, 0.598] | -0.976 | 0.109 | 0.196 |
| British Grand Prix | Ferrari | 0.096 | [-1.059, 1.244] | 0.197 | -0.121 | 0.144 |
| British Grand Prix | Mercedes | 0.651 | [-0.348, 1.499] | 0.731 | 0.491 | 0.172 |
| Canadian Grand Prix | McLaren | 1.413 | [0.304, 2.371] | 2.384 | -0.381 | 0.163 |
| Canadian Grand Prix | Red Bull Racing | -1.641 | [-2.462, -0.648] | -2.580 | 0.078 | 0.161 |
| Canadian Grand Prix | Ferrari | 0.510 | [-0.626, 1.484] | 0.422 | 0.660 | 0.265 |
| Canadian Grand Prix | Mercedes | 0.339 | [0.261, 0.417] | — | 0.339 | 0.146 |
| Dutch Grand Prix | McLaren | 0.726 | [0.257, 1.691] | 0.838 | 0.491 | 0.237 |
| Dutch Grand Prix | Red Bull Racing | 0.798 | [-0.780, 1.745] | 0.978 | 0.433 | 0.179 |
| Dutch Grand Prix | Ferrari | -1.268 | [-3.144, -0.209] | -1.492 | -0.859 | 0.102 |
| Dutch Grand Prix | Mercedes | 0.400 | [-1.105, 1.127] | 0.286 | 0.666 | 0.269 |
| Hungarian Grand Prix | McLaren | 0.477 | [-1.772, 2.938] | 0.818 | -0.136 | 0.188 |
| Hungarian Grand Prix | Red Bull Racing | 0.578 | [-1.367, 2.461] | 0.642 | 0.589 | 0.300 |
| Hungarian Grand Prix | Ferrari | 0.388 | [-1.302, 2.397] | 0.672 | -0.138 | 0.110 |
| Hungarian Grand Prix | Mercedes | -0.567 | [-3.360, 1.040] | -1.101 | 0.389 | 0.159 |
| Italian Grand Prix | McLaren | -0.057 | [-2.231, 1.696] | 0.145 | -0.372 | 0.117 |
| Italian Grand Prix | Red Bull Racing | -0.172 | [-2.111, 2.144] | -0.531 | 0.487 | 0.141 |
| Italian Grand Prix | Ferrari | 0.476 | [-1.432, 2.427] | 0.497 | 0.427 | 0.206 |
| Italian Grand Prix | Mercedes | 0.390 | [-1.707, 2.054] | 0.553 | 0.061 | 0.138 |
| Japanese Grand Prix | McLaren | -0.588 | [-1.269, 0.053] | -1.128 | 0.447 | 0.200 |
| Japanese Grand Prix | Red Bull Racing | 0.901 | [-0.056, 2.107] | 1.267 | 0.152 | 0.300 |
| Japanese Grand Prix | Ferrari | 0.075 | [-0.993, 0.972] | 0.062 | 0.097 | 0.102 |
| Japanese Grand Prix | Mercedes | 0.145 | [-0.723, 1.043] | 0.131 | 0.078 | 0.242 |
| Las Vegas Grand Prix | McLaren | -0.867 | [-5.352, 2.140] | -1.228 | -0.259 | 0.067 |
| Las Vegas Grand Prix | Red Bull Racing | 1.386 | [-1.030, 3.556] | 2.023 | 0.231 | 0.119 |
| Las Vegas Grand Prix | Ferrari | 0.935 | [-0.946, 2.724] | 1.293 | 0.297 | 0.115 |
| Las Vegas Grand Prix | Mercedes | 0.132 | [-3.212, 2.837] | 0.154 | 0.188 | 0.122 |
| Mexico City Grand Prix | McLaren | -1.675 | [-2.269, -1.032] | -2.538 | -0.072 | 0.216 |
| Mexico City Grand Prix | Red Bull Racing | 0.276 | [-0.523, 1.261] | 0.323 | 0.198 | 0.081 |
| Mexico City Grand Prix | Ferrari | 0.765 | [-0.081, 1.792] | 1.178 | 0.004 | 0.245 |
| Mexico City Grand Prix | Mercedes | 1.075 | [-0.156, 1.933] | 1.405 | 0.537 | 0.155 |
| Miami Grand Prix | McLaren | -1.830 | [-8.060, -0.363] | -2.381 | -0.749 | 0.105 |
| Miami Grand Prix | Red Bull Racing | 1.394 | [0.050, 3.719] | 1.772 | 0.676 | 0.189 |
| Miami Grand Prix | Ferrari | 0.818 | [-0.807, 2.672] | 1.110 | 0.339 | 0.168 |
| Miami Grand Prix | Mercedes | 1.075 | [-0.316, 3.324] | 1.486 | 0.293 | 0.109 |
| Monaco Grand Prix | McLaren | 0.330 | [-0.782, 1.360] | 1.118 | -1.083 | 0.089 |
| Monaco Grand Prix | Red Bull Racing | 0.022 | [-0.859, 1.007] | -0.469 | 0.798 | 0.300 |
| Monaco Grand Prix | Ferrari | 0.101 | [-0.747, 1.046] | -0.032 | 0.403 | 0.101 |
| Monaco Grand Prix | Mercedes | 0.264 | [-0.512, 1.048] | 0.245 | 0.389 | 0.158 |
| Qatar Grand Prix | McLaren | -0.045 | [-1.364, 0.888] | -0.373 | 0.576 | 0.099 |
| Qatar Grand Prix | Red Bull Racing | 0.834 | [-0.335, 1.687] | 1.429 | -0.274 | 0.211 |
| Qatar Grand Prix | Ferrari | 0.174 | [-0.555, 1.297] | 0.321 | -0.040 | 0.291 |
| Qatar Grand Prix | Mercedes | -0.022 | [-1.223, 1.056] | -0.229 | 0.368 | 0.081 |
| Saudi Arabian Grand Prix | McLaren | -0.041 | [-1.000, 1.464] | 0.283 | -0.690 | 0.201 |
| Saudi Arabian Grand Prix | Red Bull Racing | -0.086 | [-4.492, 1.314] | -0.634 | 0.891 | 0.078 |
| Saudi Arabian Grand Prix | Ferrari | 0.610 | [-0.615, 2.334] | 0.948 | -0.015 | 0.051 |
| Saudi Arabian Grand Prix | Mercedes | 0.589 | [-0.829, 2.029] | 0.761 | 0.232 | 0.092 |
| Singapore Grand Prix | McLaren | -0.034 | [-3.638, 1.494] | -0.006 | -0.093 | 0.165 |
| Singapore Grand Prix | Red Bull Racing | -0.420 | [-2.425, 0.805] | -0.665 | 0.018 | 0.088 |
| Singapore Grand Prix | Ferrari | 0.953 | [-0.832, 2.842] | 1.402 | 0.076 | 0.174 |
| Singapore Grand Prix | Mercedes | 0.777 | [-0.301, 2.948] | 0.894 | 0.621 | 0.170 |
| Spanish Grand Prix | McLaren | 0.077 | [-0.842, 0.763] | 0.231 | -0.235 | 0.300 |
| Spanish Grand Prix | Red Bull Racing | 1.317 | [0.149, 1.946] | 1.766 | 0.467 | 0.300 |
| Spanish Grand Prix | Ferrari | -0.275 | [-1.343, 0.873] | -0.549 | 0.272 | 0.182 |
| Spanish Grand Prix | Mercedes | -0.103 | [-0.967, 0.837] | -0.447 | 0.520 | 0.299 |
| São Paulo Grand Prix | McLaren | 0.609 | [-0.383, 1.481] | 0.595 | 0.603 | 0.300 |
| São Paulo Grand Prix | Red Bull Racing | -0.199 | [-0.897, 0.862] | -0.598 | 0.424 | 0.184 |
| São Paulo Grand Prix | Ferrari | -0.544 | [-1.385, 0.648] | -0.803 | -0.027 | 0.079 |
| São Paulo Grand Prix | Mercedes | 0.512 | [-0.451, 1.569] | 0.919 | -0.286 | 0.163 |
| United States Grand Prix | McLaren | -0.410 | [-1.277, 0.658] | -0.605 | -0.017 | 0.200 |
| United States Grand Prix | Red Bull Racing | 1.108 | [-0.068, 2.418] | 1.619 | 0.178 | 0.173 |
| United States Grand Prix | Ferrari | -0.906 | [-1.882, 0.275] | -1.425 | 0.049 | 0.145 |
| United States Grand Prix | Mercedes | 0.820 | [-0.454, 1.631] | 1.027 | 0.442 | 0.166 |

## 配速与赛果代理不一致（排名差 ≥ 2）

| 分站 | 车手 | 车队 | 配速排名 | classification proxy | 差值 |
| --- | --- | --- | ---: | ---: | ---: |
| Abu Dhabi Grand Prix | LEC | Ferrari | 5 | 3 | -2 |
| Australian Grand Prix | HAM | Mercedes | 4 | 2 | -2 |
| Australian Grand Prix | SAI | Ferrari | 1 | 3 | +2 |
| Azerbaijan Grand Prix | HAM | Mercedes | 3 | 5 | +2 |
| Azerbaijan Grand Prix | SAI | Ferrari | 6 | 4 | -2 |
| Bahrain Grand Prix | NOR | McLaren | 4 | 8 | +4 |
| Bahrain Grand Prix | SAI | Ferrari | 7 | 4 | -3 |
| British Grand Prix | HAM | Mercedes | 5 | 3 | -2 |
| British Grand Prix | NOR | McLaren | 4 | 2 | -2 |
| British Grand Prix | RUS | Mercedes | 1 | 5 | +4 |
| Dutch Grand Prix | NOR | McLaren | 3 | 5 | +2 |
| Dutch Grand Prix | SAI | Ferrari | 5 | 3 | -2 |
| Hungarian Grand Prix | NOR | McLaren | 4 | 2 | -2 |
| Hungarian Grand Prix | PIA | McLaren | 7 | 5 | -2 |
| Hungarian Grand Prix | RUS | Mercedes | 5 | 7 | +2 |
| Italian Grand Prix | HAM | Mercedes | 8 | 6 | -2 |
| Italian Grand Prix | LEC | Ferrari | 1 | 4 | +3 |
| Italian Grand Prix | NOR | McLaren | 5 | 7 | +2 |
| Italian Grand Prix | PIA | McLaren | 6 | 8 | +2 |
| Italian Grand Prix | RUS | Mercedes | 7 | 5 | -2 |
| Italian Grand Prix | VER | Red Bull Racing | 3 | 1 | -2 |
| Japanese Grand Prix | PIA | McLaren | 5 | 3 | -2 |
| Japanese Grand Prix | SAI | Ferrari | 3 | 6 | +3 |
| Las Vegas Grand Prix | PIA | McLaren | 4 | 7 | +3 |
| Las Vegas Grand Prix | RUS | Mercedes | 6 | 4 | -2 |
| Las Vegas Grand Prix | SAI | Ferrari | 7 | 5 | -2 |
| Las Vegas Grand Prix | VER | Red Bull Racing | 3 | 1 | -2 |
| Mexico City Grand Prix | NOR | McLaren | 3 | 5 | +2 |
| Mexico City Grand Prix | SAI | Ferrari | 6 | 4 | -2 |
| Monaco Grand Prix | HAM | Mercedes | 4 | 2 | -2 |
| Monaco Grand Prix | LEC | Ferrari | 2 | 4 | +2 |
| Monaco Grand Prix | PER | Red Bull Racing | 6 | 8 | +2 |
| Monaco Grand Prix | RUS | Mercedes | 5 | 3 | -2 |
| Monaco Grand Prix | SAI | Ferrari | 3 | 5 | +2 |
| Qatar Grand Prix | NOR | McLaren | 1 | 3 | +2 |
| Qatar Grand Prix | VER | Red Bull Racing | 3 | 1 | -2 |
| Saudi Arabian Grand Prix | RUS | Mercedes | 5 | 3 | -2 |
| Saudi Arabian Grand Prix | SAI | Ferrari | 3 | 5 | +2 |
| Singapore Grand Prix | HAM | Mercedes | 1 | 3 | +2 |
| Singapore Grand Prix | SAI | Ferrari | 7 | 1 | -6 |
| United States Grand Prix | LEC | Ferrari | 8 | 6 | -2 |

## 解释边界

- classification proxy 来自 v5 本地代理分类，不等同于经赛后处罚修订的 FIA 最终分类。
- 配速与赛果代理不一致只是一张待查清单；不能从差值直接断言策略、可靠性、事故或处罚中的哪一项造成结果。
- 同调教指 parc-fermé 周末族代理，不表示已观测到两位车手采用完全相同设置。
