# 2024 四队逐站可解释深度报告（v6）

- 性质：`retrospective_explanatory_extension_after_2025_review`；共 23 站。
- Layer A：traffic 覆盖 94.7%；特征只按 2024 时间外选择，2025 仅锁定报告。
- 方向约定：理论上限 advantage_z / 90s ms 越大越快；CI 是统计不确定性，不是物理极限范围。

## 分站总览

| 分站 | R 圈 | traffic覆盖 | dirty-air | 方差比 | 成对置信度 | 门控 | 等级 | 理论上限第一 | 90s ms | 模型↔配速 ρ | 配速↔赛果proxy ρ |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | ---: |
| Abu Dhabi Grand Prix | 341 | 85.0% | 24.3% | 0.646 | 0.833 | pass | medium | McLaren | 768.0 | 0.857 | 0.857 |
| Australian Grand Prix | 309 | 83.8% | 23.0% | 0.696 | 0.872 | pass | high | Ferrari | 941.6 | 0.952 | 0.905 |
| Austrian Grand Prix | 493 | 87.8% | 29.2% | 0.057 | 0.745 | pass | low | Ferrari | 648.7 | 0.857 | -0.190 |
| Azerbaijan Grand Prix | 357 | 88.0% | 59.1% | 0.115 | 0.661 | pass | low | McLaren | 1701.1 | 0.690 | 0.357 |
| Bahrain Grand Prix | 396 | 87.6% | 26.8% | 0.344 | 0.813 | pass | medium | Red Bull Racing | 525.4 | 0.905 | 0.905 |
| Belgian Grand Prix | 300 | 88.3% | 45.7% | 0.245 | 0.736 | pass | low | McLaren | 441.6 | 0.976 | 0.262 |
| British Grand Prix | 253 | 87.0% | 43.5% | 0.541 | 0.787 | pass | medium | Ferrari | 834.0 | 0.833 | 0.690 |
| Canadian Grand Prix | 81 | 85.2% | 50.6% | 0.844 | 0.617 | fail | low | Mercedes | 1659.2 | 0.286 | 0.821 |
| Chinese Grand Prix | 334 | 87.7% | 44.6% | 0.251 | 0.770 | pass | medium | Red Bull Racing | 1076.6 | 0.833 | 0.929 |
| Dutch Grand Prix | 547 | 87.8% | 34.6% | 0.190 | 0.825 | pass | low | Ferrari | 705.6 | 0.667 | 0.381 |
| Emilia Romagna Grand Prix | 471 | 88.1% | 28.9% | 0.149 | 0.772 | pass | low | Red Bull Racing | 858.7 | 0.905 | 0.714 |
| Hungarian Grand Prix | 509 | 88.8% | 37.9% | 0.042 | 0.681 | pass | low | McLaren | 1523.7 | 0.857 | 0.881 |
| Italian Grand Prix | 385 | 88.1% | 35.1% | 0.366 | 0.827 | pass | medium | McLaren | 342.7 | 0.929 | 0.786 |
| Japanese Grand Prix | 354 | 87.9% | 38.1% | 0.082 | 0.624 | pass | low | Mercedes | 554.7 | 0.786 | 0.952 |
| Las Vegas Grand Prix | 344 | 87.8% | 43.0% | 0.363 | 0.804 | pass | medium | McLaren | 1543.9 | 0.976 | 0.833 |
| Mexico City Grand Prix | 489 | 87.5% | 35.4% | 0.766 | 0.910 | pass | high | Ferrari | 864.3 | 0.905 | 0.881 |
| Miami Grand Prix | 382 | 87.4% | 47.6% | 0.266 | 0.825 | pass | medium | Red Bull Racing | 940.7 | 0.833 | 0.738 |
| Monaco Grand Prix | 520 | 85.6% | 60.2% | 0.018 | 0.762 | pass | low | McLaren | 556.5 | 0.750 | 0.071 |
| Qatar Grand Prix | 311 | 86.2% | 44.1% | 0.257 | 0.782 | pass | medium | Red Bull Racing | 803.7 | 0.857 | 0.548 |
| Saudi Arabian Grand Prix | 355 | 87.6% | 40.3% | 0.193 | 0.725 | pass | low | Red Bull Racing | 854.8 | 0.976 | 0.810 |
| Singapore Grand Prix | 464 | 87.3% | 38.4% | 0.215 | 0.759 | pass | low | Ferrari | 1167.4 | 0.738 | 0.952 |
| Spanish Grand Prix | 480 | 87.9% | 35.0% | 0.085 | 0.699 | pass | low | Ferrari | 191.9 | 0.881 | 0.714 |
| United States Grand Prix | 324 | 85.8% | 25.6% | 0.746 | 0.820 | pass | medium | Mercedes | 1182.8 | 1.000 | 0.929 |

## 三种排序（从快到慢）

| 分站 | 模型车手组合序 | 观测干净配速序 | classification proxy 序 |
| --- | --- | --- | --- |
| Abu Dhabi Grand Prix | NOR > LEC > SAI > PIA > VER > HAM > RUS | NOR > SAI > LEC > HAM > PIA > VER > RUS | NOR > SAI > LEC > HAM > RUS > VER > PIA |
| Australian Grand Prix | LEC > SAI > NOR > PIA > VER > PER > HAM > RUS | SAI > LEC > NOR > PIA > PER > VER > HAM > RUS | SAI > LEC > NOR > PIA > PER > RUS > HAM > VER |
| Austrian Grand Prix | NOR > PIA > VER > LEC > SAI > HAM > RUS > PER | VER > NOR > LEC > PIA > SAI > RUS > HAM > PER | RUS > PIA > SAI > HAM > VER > NOR > PER > LEC |
| Azerbaijan Grand Prix | VER > LEC > NOR > SAI > PER > PIA > HAM > RUS | LEC > PER > SAI > VER > NOR > PIA > RUS > HAM | PIA > LEC > PER > RUS > NOR > SAI > VER > HAM |
| Bahrain Grand Prix | VER > LEC > PER > SAI > NOR > HAM > RUS > PIA | VER > PER > SAI > LEC > HAM > NOR > RUS > PIA | VER > PER > SAI > LEC > RUS > NOR > HAM > PIA |
| Belgian Grand Prix | NOR > VER > HAM > PIA > RUS > LEC > SAI > PER | NOR > VER > HAM > PIA > RUS > SAI > LEC > PER | RUS > HAM > PIA > LEC > VER > NOR > SAI > PER |
| British Grand Prix | NOR > PIA > HAM > RUS > LEC > VER > SAI > PER | PIA > HAM > RUS > NOR > VER > LEC > SAI > PER | HAM > VER > NOR > PIA > RUS > SAI > LEC > PER |
| Canadian Grand Prix | NOR > VER > PIA > SAI > HAM > RUS > PER | RUS > VER > NOR > PIA > HAM > PER > SAI | VER > NOR > RUS > HAM > PIA > SAI > PER |
| Chinese Grand Prix | VER > PER > NOR > LEC > SAI > PIA > HAM > RUS | VER > PER > NOR > LEC > RUS > SAI > HAM > PIA | VER > NOR > PER > LEC > SAI > RUS > PIA > HAM |
| Dutch Grand Prix | NOR > PIA > LEC > VER > SAI > HAM > RUS > PER | NOR > HAM > PIA > LEC > VER > PER > SAI > RUS | NOR > VER > LEC > PIA > SAI > PER > RUS > HAM |
| Emilia Romagna Grand Prix | NOR > PIA > LEC > VER > SAI > HAM > RUS > PER | PIA > NOR > LEC > VER > RUS > SAI > HAM > PER | VER > NOR > LEC > PIA > SAI > HAM > RUS > PER |
| Hungarian Grand Prix | NOR > VER > PIA > LEC > HAM > SAI > RUS > PER | NOR > PIA > LEC > VER > HAM > PER > SAI > RUS | PIA > NOR > HAM > LEC > VER > SAI > PER > RUS |
| Italian Grand Prix | NOR > PIA > LEC > SAI > HAM > RUS > VER > PER | NOR > PIA > LEC > HAM > RUS > SAI > VER > PER | LEC > PIA > NOR > SAI > HAM > VER > RUS > PER |
| Japanese Grand Prix | VER > NOR > PER > LEC > PIA > SAI > HAM > RUS | VER > PER > SAI > NOR > LEC > PIA > RUS > HAM | VER > PER > SAI > LEC > NOR > RUS > PIA > HAM |
| Las Vegas Grand Prix | LEC > HAM > SAI > RUS > VER > NOR > PIA > PER | HAM > LEC > SAI > RUS > VER > NOR > PIA > PER | RUS > HAM > SAI > LEC > VER > NOR > PIA > PER |
| Mexico City Grand Prix | LEC > NOR > SAI > PIA > HAM > VER > RUS > PER | NOR > SAI > LEC > HAM > PIA > VER > RUS > PER | SAI > NOR > LEC > HAM > RUS > VER > PIA > PER |
| Miami Grand Prix | LEC > NOR > SAI > VER > PIA > PER > HAM > RUS | LEC > NOR > SAI > HAM > VER > PER > PIA > RUS | NOR > VER > LEC > SAI > PER > HAM > RUS > PIA |
| Monaco Grand Prix | NOR > PIA > LEC > SAI > VER > HAM > RUS | NOR > PIA > VER > SAI > HAM > LEC > RUS | LEC > PIA > SAI > NOR > RUS > VER > HAM |
| Qatar Grand Prix | NOR > PIA > VER > LEC > SAI > PER > HAM > RUS | VER > NOR > PIA > LEC > SAI > RUS > PER > HAM | VER > LEC > PIA > RUS > PER > SAI > NOR > HAM |
| Saudi Arabian Grand Prix | VER > PER > LEC > NOR > PIA > HAM > RUS > BEA | VER > PER > LEC > PIA > NOR > HAM > RUS > BEA | VER > PER > LEC > PIA > RUS > BEA > NOR > HAM |
| Singapore Grand Prix | NOR > PIA > VER > LEC > SAI > PER > HAM > RUS | NOR > VER > PIA > LEC > RUS > HAM > PER > SAI | NOR > VER > PIA > RUS > LEC > HAM > SAI > PER |
| Spanish Grand Prix | NOR > VER > HAM > PIA > LEC > RUS > SAI > PER | NOR > HAM > LEC > VER > PIA > RUS > SAI > PER | VER > NOR > HAM > RUS > LEC > SAI > PIA > PER |
| United States Grand Prix | LEC > NOR > SAI > PIA > VER > RUS > PER | LEC > NOR > SAI > PIA > VER > RUS > PER | LEC > SAI > NOR > VER > PIA > RUS > PER |

## 每队理论上限

| 分站 | 车队 | 融合 advantage_z p50 | CI80 | Q p50 | R p50 | 执行校正 R p50 |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| Abu Dhabi Grand Prix | McLaren | 0.857 | [0.240, 1.434] | 1.254 | 0.116 | 0.064 |
| Abu Dhabi Grand Prix | Red Bull Racing | -0.168 | [-0.916, 0.930] | -0.096 | -0.245 | 0.102 |
| Abu Dhabi Grand Prix | Ferrari | 0.355 | [-0.345, 0.890] | 0.442 | 0.213 | 0.079 |
| Abu Dhabi Grand Prix | Mercedes | -0.658 | [-1.305, -0.017] | -1.130 | 0.260 | 0.100 |
| Australian Grand Prix | McLaren | 0.208 | [-0.868, 1.601] | 0.151 | 0.291 | 0.080 |
| Australian Grand Prix | Red Bull Racing | -0.197 | [-1.198, 1.184] | -0.154 | -0.189 | 0.125 |
| Australian Grand Prix | Ferrari | 1.052 | [-0.190, 2.201] | 1.331 | 0.521 | 0.102 |
| Australian Grand Prix | Mercedes | -0.680 | [-1.912, 0.621] | -0.964 | -0.132 | 0.159 |
| Austrian Grand Prix | McLaren | 0.226 | [-1.393, 1.569] | 0.078 | 0.531 | 0.300 |
| Austrian Grand Prix | Red Bull Racing | 0.174 | [-1.131, 1.383] | 0.322 | -0.056 | 0.101 |
| Austrian Grand Prix | Ferrari | 0.723 | [-0.578, 1.818] | 1.052 | 0.130 | 0.237 |
| Austrian Grand Prix | Mercedes | -0.215 | [-1.891, 1.262] | -0.412 | 0.183 | 0.210 |
| Azerbaijan Grand Prix | McLaren | 1.908 | [0.850, 3.007] | 2.820 | 0.226 | 0.185 |
| Azerbaijan Grand Prix | Red Bull Racing | -0.095 | [-0.971, 1.090] | -0.228 | 0.131 | 0.157 |
| Azerbaijan Grand Prix | Ferrari | -1.367 | [-3.172, -0.233] | -2.241 | 0.247 | 0.085 |
| Azerbaijan Grand Prix | Mercedes | 0.198 | [-1.331, 1.526] | 0.355 | -0.103 | 0.101 |
| Bahrain Grand Prix | McLaren | 0.132 | [-0.615, 0.991] | 0.320 | -0.155 | 0.229 |
| Bahrain Grand Prix | Red Bull Racing | 0.586 | [-0.412, 1.820] | 0.637 | 0.466 | 0.300 |
| Bahrain Grand Prix | Ferrari | 0.162 | [-0.889, 1.161] | 0.019 | 0.462 | 0.203 |
| Bahrain Grand Prix | Mercedes | -0.249 | [-1.185, 0.715] | -0.444 | 0.163 | 0.270 |
| Belgian Grand Prix | McLaren | 0.492 | [0.306, 0.634] | — | 0.492 | 0.260 |
| Belgian Grand Prix | Red Bull Racing | 0.102 | [-0.044, 0.258] | — | 0.102 | 0.235 |
| Belgian Grand Prix | Ferrari | -0.165 | [-0.256, -0.041] | — | -0.165 | 0.076 |
| Belgian Grand Prix | Mercedes | 0.327 | [0.159, 0.456] | — | 0.327 | 0.254 |
| British Grand Prix | McLaren | 0.258 | [-1.268, 1.547] | 0.116 | 0.461 | 0.196 |
| British Grand Prix | Red Bull Racing | -0.423 | [-1.492, 1.043] | -0.632 | -0.096 | 0.285 |
| British Grand Prix | Ferrari | 0.931 | [-0.262, 1.940] | 1.453 | 0.089 | 0.240 |
| British Grand Prix | Mercedes | -0.035 | [-0.967, 0.921] | -0.188 | 0.306 | 0.108 |
| Canadian Grand Prix | McLaren | 1.658 | [0.386, 3.132] | 1.983 | 0.948 | 0.035 |
| Canadian Grand Prix | Red Bull Racing | -2.221 | [-6.534, -0.962] | -2.941 | -0.494 | 0.022 |
| Canadian Grand Prix | Ferrari | 0.158 | [-1.144, 1.715] | 1.603 | -2.580 | 0.000 |
| Canadian Grand Prix | Mercedes | 1.861 | [0.747, 3.619] | 1.570 | 2.417 | 0.091 |
| Chinese Grand Prix | McLaren | -0.137 | [-1.034, 0.949] | -0.296 | 0.130 | 0.248 |
| Chinese Grand Prix | Red Bull Racing | 1.203 | [-0.476, 2.479] | 1.494 | 0.675 | 0.063 |
| Chinese Grand Prix | Ferrari | 0.146 | [-0.833, 1.092] | 0.164 | 0.068 | 0.226 |
| Chinese Grand Prix | Mercedes | -0.394 | [-1.361, 0.524] | -0.510 | -0.204 | 0.161 |
| Dutch Grand Prix | McLaren | 0.015 | [-0.632, 0.968] | -0.200 | 0.405 | 0.130 |
| Dutch Grand Prix | Red Bull Racing | -0.920 | [-1.465, -0.361] | -1.204 | -0.361 | 0.080 |
| Dutch Grand Prix | Ferrari | 0.787 | [-0.236, 1.793] | 1.080 | 0.266 | 0.278 |
| Dutch Grand Prix | Mercedes | 0.392 | [-0.362, 1.626] | 0.457 | 0.317 | 0.189 |
| Emilia Romagna Grand Prix | McLaren | 0.598 | [-0.405, 1.501] | 0.666 | 0.439 | 0.181 |
| Emilia Romagna Grand Prix | Red Bull Racing | 0.959 | [-0.119, 1.813] | 1.520 | -0.093 | 0.159 |
| Emilia Romagna Grand Prix | Ferrari | -0.945 | [-2.001, -0.075] | -1.610 | 0.327 | 0.195 |
| Emilia Romagna Grand Prix | Mercedes | 0.188 | [-0.467, 1.072] | 0.265 | 0.085 | 0.255 |
| Hungarian Grand Prix | McLaren | 1.707 | [0.801, 3.467] | 2.472 | 0.306 | 0.257 |
| Hungarian Grand Prix | Red Bull Racing | 0.240 | [-3.070, 1.465] | 0.240 | 0.263 | 0.191 |
| Hungarian Grand Prix | Ferrari | -0.188 | [-1.633, 1.409] | -0.358 | 0.128 | 0.212 |
| Hungarian Grand Prix | Mercedes | -0.364 | [-3.011, 1.079] | -0.660 | 0.203 | 0.300 |
| Italian Grand Prix | McLaren | 0.381 | [-0.542, 1.076] | 0.355 | 0.455 | 0.153 |
| Italian Grand Prix | Red Bull Racing | 0.162 | [-0.805, 0.942] | 0.479 | -0.408 | 0.223 |
| Italian Grand Prix | Ferrari | -0.223 | [-1.244, 1.647] | -0.404 | 0.112 | 0.116 |
| Italian Grand Prix | Mercedes | 0.137 | [-0.822, 0.935] | -0.032 | 0.492 | 0.185 |
| Japanese Grand Prix | McLaren | -0.223 | [-0.840, 0.217] | -0.333 | 0.008 | 0.149 |
| Japanese Grand Prix | Red Bull Racing | 0.225 | [-0.340, 0.699] | 0.114 | 0.436 | 0.300 |
| Japanese Grand Prix | Ferrari | -0.134 | [-0.667, 0.416] | -0.309 | 0.212 | 0.244 |
| Japanese Grand Prix | Mercedes | 0.618 | [-0.306, 1.817] | 0.912 | 0.172 | 0.179 |
| Las Vegas Grand Prix | McLaren | 1.730 | [0.710, 3.335] | 2.749 | -0.089 | 0.159 |
| Las Vegas Grand Prix | Red Bull Racing | -0.030 | [-2.063, 1.612] | 0.083 | -0.213 | 0.139 |
| Las Vegas Grand Prix | Ferrari | 0.448 | [-1.120, 1.998] | 0.445 | 0.421 | 0.163 |
| Las Vegas Grand Prix | Mercedes | -1.063 | [-4.005, 0.632] | -1.881 | 0.478 | 0.126 |
| Mexico City Grand Prix | McLaren | 0.151 | [-2.688, 1.239] | -0.011 | 0.413 | 0.174 |
| Mexico City Grand Prix | Red Bull Racing | -0.689 | [-2.157, 0.643] | -0.813 | -0.422 | 0.209 |
| Mexico City Grand Prix | Ferrari | 0.965 | [-0.758, 2.630] | 1.165 | 0.596 | 0.273 |
| Mexico City Grand Prix | Mercedes | 0.902 | [-0.656, 2.678] | 1.307 | 0.145 | 0.087 |
| Miami Grand Prix | McLaren | 0.445 | [-0.937, 1.611] | 0.620 | 0.158 | 0.084 |
| Miami Grand Prix | Red Bull Racing | 1.051 | [-0.742, 2.638] | 1.701 | -0.129 | 0.160 |
| Miami Grand Prix | Ferrari | 0.528 | [-1.076, 1.828] | 0.628 | 0.351 | 0.142 |
| Miami Grand Prix | Mercedes | -1.401 | [-2.426, -0.170] | -2.174 | 0.097 | 0.091 |
| Monaco Grand Prix | McLaren | 0.620 | [-0.513, 1.369] | 1.016 | -0.149 | 0.295 |
| Monaco Grand Prix | Red Bull Racing | -0.135 | [-1.492, 0.829] | -0.501 | 0.537 | 0.109 |
| Monaco Grand Prix | Ferrari | 0.054 | [-0.815, 0.962] | 0.143 | -0.033 | 0.300 |
| Monaco Grand Prix | Mercedes | 0.576 | [-0.430, 1.496] | 0.526 | 0.640 | 0.300 |
| Qatar Grand Prix | McLaren | -0.048 | [-1.437, 0.857] | -0.417 | 0.599 | 0.124 |
| Qatar Grand Prix | Red Bull Racing | 0.897 | [-0.130, 1.868] | 1.536 | -0.307 | 0.060 |
| Qatar Grand Prix | Ferrari | -0.093 | [-1.174, 1.141] | -0.253 | 0.195 | 0.094 |
| Qatar Grand Prix | Mercedes | 0.402 | [-0.716, 1.328] | 0.678 | -0.093 | 0.129 |
| Saudi Arabian Grand Prix | McLaren | -0.326 | [-1.329, 0.776] | -0.442 | -0.134 | 0.099 |
| Saudi Arabian Grand Prix | Red Bull Racing | 0.954 | [-0.203, 2.013] | 1.317 | 0.216 | 0.142 |
| Saudi Arabian Grand Prix | Ferrari | 0.217 | [-1.018, 1.285] | 0.250 | 0.168 | 0.060 |
| Saudi Arabian Grand Prix | Mercedes | -0.147 | [-1.256, 0.779] | -0.293 | 0.159 | 0.134 |
| Singapore Grand Prix | McLaren | 0.671 | [-0.473, 2.210] | 0.663 | 0.703 | 0.153 |
| Singapore Grand Prix | Red Bull Racing | -0.904 | [-1.727, 0.280] | -1.259 | -0.273 | 0.093 |
| Singapore Grand Prix | Ferrari | 1.306 | [-0.233, 2.186] | 1.950 | 0.150 | 0.154 |
| Singapore Grand Prix | Mercedes | -0.515 | [-1.276, 0.373] | -0.715 | -0.101 | 0.087 |
| Spanish Grand Prix | McLaren | 0.032 | [-0.367, 0.325] | -0.212 | 0.464 | 0.240 |
| Spanish Grand Prix | Red Bull Racing | 0.064 | [-0.421, 0.416] | 0.031 | 0.136 | 0.300 |
| Spanish Grand Prix | Ferrari | 0.213 | [-0.168, 0.533] | 0.363 | -0.062 | 0.114 |
| Spanish Grand Prix | Mercedes | 0.153 | [-0.461, 1.168] | 0.060 | 0.338 | 0.294 |
| United States Grand Prix | McLaren | -0.298 | [-1.815, 1.203] | -0.520 | 0.158 | 0.077 |
| United States Grand Prix | Red Bull Racing | 0.227 | [-1.031, 1.328] | 0.542 | -0.308 | 0.221 |
| United States Grand Prix | Ferrari | -0.570 | [-2.148, 1.029] | -1.156 | 0.455 | 0.123 |
| United States Grand Prix | Mercedes | 1.323 | [0.031, 2.390] | 1.947 | 0.191 | 0.090 |

## 配速与赛果代理不一致（排名差 ≥ 2）

| 分站 | 车手 | 车队 | 配速排名 | classification proxy | 差值 |
| --- | --- | --- | ---: | ---: | ---: |
| Abu Dhabi Grand Prix | PIA | McLaren | 5 | 7 | +2 |
| Abu Dhabi Grand Prix | RUS | Mercedes | 7 | 5 | -2 |
| Australian Grand Prix | RUS | Mercedes | 8 | 6 | -2 |
| Australian Grand Prix | VER | Red Bull Racing | 6 | 8 | +2 |
| Austrian Grand Prix | HAM | Mercedes | 7 | 4 | -3 |
| Austrian Grand Prix | LEC | Ferrari | 3 | 8 | +5 |
| Austrian Grand Prix | NOR | McLaren | 2 | 6 | +4 |
| Austrian Grand Prix | PIA | McLaren | 4 | 2 | -2 |
| Austrian Grand Prix | RUS | Mercedes | 6 | 1 | -5 |
| Austrian Grand Prix | SAI | Ferrari | 5 | 3 | -2 |
| Austrian Grand Prix | VER | Red Bull Racing | 1 | 5 | +4 |
| Azerbaijan Grand Prix | PIA | McLaren | 6 | 1 | -5 |
| Azerbaijan Grand Prix | RUS | Mercedes | 7 | 4 | -3 |
| Azerbaijan Grand Prix | SAI | Ferrari | 3 | 6 | +3 |
| Azerbaijan Grand Prix | VER | Red Bull Racing | 4 | 7 | +3 |
| Bahrain Grand Prix | HAM | Mercedes | 5 | 7 | +2 |
| Bahrain Grand Prix | RUS | Mercedes | 7 | 5 | -2 |
| Belgian Grand Prix | LEC | Ferrari | 7 | 4 | -3 |
| Belgian Grand Prix | NOR | McLaren | 1 | 6 | +5 |
| Belgian Grand Prix | RUS | Mercedes | 5 | 1 | -4 |
| Belgian Grand Prix | VER | Red Bull Racing | 2 | 5 | +3 |
| British Grand Prix | PIA | McLaren | 1 | 4 | +3 |
| British Grand Prix | RUS | Mercedes | 3 | 5 | +2 |
| British Grand Prix | VER | Red Bull Racing | 5 | 2 | -3 |
| Canadian Grand Prix | RUS | Mercedes | 1 | 3 | +2 |
| Dutch Grand Prix | HAM | Mercedes | 2 | 8 | +6 |
| Dutch Grand Prix | SAI | Ferrari | 7 | 5 | -2 |
| Dutch Grand Prix | VER | Red Bull Racing | 5 | 2 | -3 |
| Emilia Romagna Grand Prix | PIA | McLaren | 1 | 4 | +3 |
| Emilia Romagna Grand Prix | RUS | Mercedes | 5 | 7 | +2 |
| Emilia Romagna Grand Prix | VER | Red Bull Racing | 4 | 1 | -3 |
| Hungarian Grand Prix | HAM | Mercedes | 5 | 3 | -2 |
| Italian Grand Prix | LEC | Ferrari | 3 | 1 | -2 |
| Italian Grand Prix | NOR | McLaren | 1 | 3 | +2 |
| Italian Grand Prix | RUS | Mercedes | 5 | 7 | +2 |
| Italian Grand Prix | SAI | Ferrari | 6 | 4 | -2 |
| Las Vegas Grand Prix | LEC | Ferrari | 2 | 4 | +2 |
| Las Vegas Grand Prix | RUS | Mercedes | 4 | 1 | -3 |
| Mexico City Grand Prix | PIA | McLaren | 5 | 7 | +2 |
| Mexico City Grand Prix | RUS | Mercedes | 7 | 5 | -2 |
| Miami Grand Prix | HAM | Mercedes | 4 | 6 | +2 |
| Miami Grand Prix | LEC | Ferrari | 1 | 3 | +2 |
| Miami Grand Prix | VER | Red Bull Racing | 5 | 2 | -3 |
| Monaco Grand Prix | HAM | Mercedes | 5 | 7 | +2 |
| Monaco Grand Prix | LEC | Ferrari | 6 | 1 | -5 |
| Monaco Grand Prix | NOR | McLaren | 1 | 4 | +3 |
| Monaco Grand Prix | RUS | Mercedes | 7 | 5 | -2 |
| Monaco Grand Prix | VER | Red Bull Racing | 3 | 6 | +3 |
| Qatar Grand Prix | LEC | Ferrari | 4 | 2 | -2 |
| Qatar Grand Prix | NOR | McLaren | 2 | 7 | +5 |
| Qatar Grand Prix | PER | Red Bull Racing | 7 | 5 | -2 |
| Qatar Grand Prix | RUS | Mercedes | 6 | 4 | -2 |
| Saudi Arabian Grand Prix | BEA | Ferrari | 8 | 6 | -2 |
| Saudi Arabian Grand Prix | HAM | Mercedes | 6 | 8 | +2 |
| Saudi Arabian Grand Prix | NOR | McLaren | 5 | 7 | +2 |
| Saudi Arabian Grand Prix | RUS | Mercedes | 7 | 5 | -2 |
| Spanish Grand Prix | LEC | Ferrari | 3 | 5 | +2 |
| Spanish Grand Prix | PIA | McLaren | 5 | 7 | +2 |
| Spanish Grand Prix | RUS | Mercedes | 6 | 4 | -2 |
| Spanish Grand Prix | VER | Red Bull Racing | 4 | 1 | -3 |

## 解释边界

- classification proxy 来自 v5 本地代理分类，不等同于经赛后处罚修订的 FIA 最终分类。
- 配速与赛果代理不一致只是一张待查清单；不能从差值直接断言策略、可靠性、事故或处罚中的哪一项造成结果。
- 同调教指 parc-fermé 周末族代理，不表示已观测到两位车手采用完全相同设置。
