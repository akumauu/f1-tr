# 2025 四队逐站可解释深度报告（v6）

- 性质：`retrospective_explanatory_extension_after_2025_review`；共 24 站。
- Layer A：traffic 覆盖 94.7%；特征只按 2024 时间外选择，2025 仅锁定报告。
- 方向约定：理论上限 advantage_z / 90s ms 越大越快；CI 是统计不确定性，不是物理极限范围。

## 分站总览

| 分站 | R 圈 | traffic覆盖 | dirty-air | 方差比 | 成对置信度 | 门控 | 等级 | 理论上限第一 | 90s ms | 模型↔配速 ρ | 配速↔赛果proxy ρ |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | ---: |
| Abu Dhabi Grand Prix | 421 | 87.9% | 43.7% | 0.283 | 0.811 | pass | medium | McLaren | 639.3 | 0.857 | 0.738 |
| Australian Grand Prix | 16 | 87.5% | 75.0% | 1.304 | 0.699 | fail | low | McLaren | 1081.7 | 0.405 | 0.238 |
| Austrian Grand Prix | 364 | 84.3% | 21.2% | 0.861 | 0.888 | pass | high | McLaren | 1037.7 | 0.943 | 0.943 |
| Azerbaijan Grand Prix | 291 | 85.6% | 55.3% | 0.159 | 0.698 | pass | low | Red Bull Racing | 856.6 | 0.893 | 0.750 |
| Bahrain Grand Prix | 362 | 87.3% | 62.7% | 0.056 | 0.716 | pass | low | McLaren | 501.2 | 0.881 | 0.452 |
| Belgian Grand Prix | 230 | 87.0% | 37.0% | 0.382 | 0.758 | pass | medium | McLaren | 1151.2 | 0.952 | 0.833 |
| British Grand Prix | 53 | 86.8% | 9.4% | 0.247 | 0.693 | fail | low | Red Bull Racing | 857.1 | 0.750 | 0.964 |
| Canadian Grand Prix | 472 | 88.6% | 35.6% | 0.660 | 0.892 | pass | high | Mercedes | 1255.6 | 0.548 | 0.690 |
| Chinese Grand Prix | 403 | 87.8% | 29.8% | 0.102 | 0.734 | pass | low | Mercedes | 1713.8 | 0.905 | 0.786 |
| Dutch Grand Prix | 381 | 85.8% | 58.3% | 0.472 | 0.861 | pass | medium | McLaren | 1290.3 | 0.952 | 0.810 |
| Emilia Romagna Grand Prix | 386 | 86.8% | 50.8% | 0.355 | 0.822 | pass | medium | Red Bull Racing | 730.1 | 1.000 | 0.905 |
| Hungarian Grand Prix | 519 | 88.2% | 50.3% | 0.685 | 0.922 | pass | high | McLaren | 903.3 | 0.929 | 0.952 |
| Italian Grand Prix | 385 | 87.8% | 25.5% | 0.221 | 0.732 | pass | low | Ferrari | 374.7 | 0.929 | 1.000 |
| Japanese Grand Prix | 395 | 87.8% | 47.8% | 0.221 | 0.802 | pass | low | McLaren | 464.5 | 0.857 | 0.738 |
| Las Vegas Grand Prix | 346 | 87.6% | 33.8% | 0.052 | 0.666 | pass | low | McLaren | 204.2 | 0.619 | 0.714 |
| Mexico City Grand Prix | 495 | 87.1% | 47.1% | 0.388 | 0.840 | pass | medium | McLaren | 342.3 | 0.786 | 0.190 |
| Miami Grand Prix | 232 | 87.5% | 21.1% | 0.801 | 0.817 | pass | medium | Ferrari | 957.0 | 0.976 | 0.810 |
| Monaco Grand Prix | 506 | 87.7% | 50.0% | 0.844 | 0.886 | pass | high | McLaren | 538.1 | 0.786 | 0.857 |
| Qatar Grand Prix | 385 | 88.3% | 55.8% | 0.745 | 0.896 | pass | high | Red Bull Racing | 975.5 | 0.905 | 0.905 |
| Saudi Arabian Grand Prix | 312 | 85.9% | 18.3% | 0.305 | 0.731 | pass | medium | McLaren | 311.9 | 0.750 | 0.964 |
| Singapore Grand Prix | 444 | 87.4% | 39.9% | 0.315 | 0.797 | pass | medium | Ferrari | 756.9 | 0.238 | 0.333 |
| Spanish Grand Prix | 425 | 87.3% | 28.9% | 0.125 | 0.772 | pass | low | McLaren | 622.3 | 0.857 | 0.690 |
| São Paulo Grand Prix | 378 | 85.7% | 28.0% | 0.552 | 0.672 | pass | low | Red Bull Racing | 646.3 | 0.821 | 0.750 |
| United States Grand Prix | 372 | 87.1% | 27.4% | 0.069 | 0.645 | pass | low | McLaren | 394.3 | 0.952 | 0.952 |

## 三种排序（从快到慢）

| 分站 | 模型车手组合序 | 观测干净配速序 | classification proxy 序 |
| --- | --- | --- | --- |
| Abu Dhabi Grand Prix | NOR > VER > PIA > LEC > HAM > RUS > ANT > TSU | LEC > NOR > VER > PIA > HAM > RUS > ANT > TSU | VER > PIA > NOR > LEC > RUS > HAM > TSU > ANT |
| Australian Grand Prix | NOR > PIA > VER > LEC > HAM > RUS > ANT > LAW | NOR > LAW > VER > PIA > HAM > RUS > LEC > ANT | NOR > VER > RUS > ANT > LEC > LAW > PIA > HAM |
| Austrian Grand Prix | NOR > PIA > LEC > HAM > RUS > TSU | PIA > NOR > LEC > HAM > RUS > TSU | NOR > PIA > LEC > HAM > RUS > TSU |
| Azerbaijan Grand Prix | VER > NOR > RUS > LEC > ANT > HAM > TSU | VER > RUS > NOR > ANT > LEC > TSU > HAM | VER > RUS > ANT > TSU > NOR > HAM > LEC |
| Bahrain Grand Prix | NOR > PIA > VER > LEC > RUS > HAM > ANT > TSU | NOR > PIA > LEC > VER > ANT > HAM > RUS > TSU | PIA > RUS > NOR > LEC > HAM > VER > TSU > ANT |
| Belgian Grand Prix | NOR > VER > PIA > LEC > HAM > RUS > ANT > TSU | NOR > PIA > VER > LEC > HAM > ANT > RUS > TSU | PIA > NOR > LEC > VER > RUS > HAM > TSU > ANT |
| British Grand Prix | NOR > PIA > VER > RUS > LEC > HAM > TSU | NOR > PIA > HAM > VER > LEC > RUS > TSU | NOR > PIA > HAM > VER > RUS > LEC > TSU |
| Canadian Grand Prix | RUS > NOR > PIA > ANT > VER > LEC > HAM > TSU | ANT > VER > PIA > NOR > RUS > LEC > HAM > TSU | RUS > VER > ANT > PIA > LEC > NOR > HAM > TSU |
| Chinese Grand Prix | NOR > PIA > VER > LEC > HAM > RUS > ANT > LAW | NOR > VER > PIA > HAM > RUS > LEC > ANT > LAW | PIA > NOR > RUS > VER > LEC > HAM > ANT > LAW |
| Dutch Grand Prix | NOR > PIA > LEC > VER > HAM > RUS > ANT > TSU | NOR > PIA > VER > LEC > HAM > ANT > RUS > TSU | PIA > NOR > VER > RUS > LEC > ANT > HAM > TSU |
| Emilia Romagna Grand Prix | NOR > PIA > VER > LEC > HAM > RUS > ANT > TSU | NOR > PIA > VER > LEC > HAM > RUS > ANT > TSU | VER > NOR > PIA > HAM > LEC > RUS > ANT > TSU |
| Hungarian Grand Prix | NOR > PIA > RUS > VER > LEC > ANT > HAM > TSU | PIA > NOR > RUS > LEC > VER > HAM > ANT > TSU | NOR > PIA > RUS > LEC > VER > ANT > HAM > TSU |
| Italian Grand Prix | NOR > PIA > VER > LEC > RUS > HAM > ANT > TSU | VER > NOR > PIA > LEC > RUS > HAM > ANT > TSU | VER > NOR > PIA > LEC > RUS > HAM > ANT > TSU |
| Japanese Grand Prix | NOR > VER > PIA > RUS > LEC > ANT > HAM > TSU | PIA > NOR > RUS > VER > ANT > LEC > HAM > TSU | VER > NOR > PIA > LEC > RUS > ANT > HAM > TSU |
| Las Vegas Grand Prix | VER > NOR > PIA > LEC > RUS > ANT > HAM > TSU | ANT > VER > NOR > LEC > PIA > RUS > HAM > TSU | VER > NOR > RUS > ANT > PIA > LEC > HAM > TSU |
| Mexico City Grand Prix | NOR > PIA > RUS > VER > ANT > LEC > HAM > TSU | PIA > RUS > ANT > NOR > VER > HAM > LEC > TSU | NOR > LEC > VER > PIA > ANT > RUS > HAM > TSU |
| Miami Grand Prix | NOR > PIA > VER > LEC > HAM > RUS > ANT > TSU | NOR > PIA > VER > LEC > RUS > HAM > ANT > TSU | PIA > NOR > RUS > VER > ANT > LEC > HAM > TSU |
| Monaco Grand Prix | NOR > PIA > LEC > HAM > VER > RUS > TSU > ANT | LEC > PIA > VER > NOR > HAM > RUS > TSU > ANT | NOR > LEC > PIA > VER > HAM > RUS > TSU > ANT |
| Qatar Grand Prix | NOR > PIA > VER > RUS > ANT > LEC > TSU > HAM | PIA > NOR > VER > ANT > LEC > RUS > TSU > HAM | VER > PIA > NOR > ANT > RUS > LEC > TSU > HAM |
| Saudi Arabian Grand Prix | NOR > VER > PIA > LEC > HAM > RUS > ANT | PIA > VER > NOR > LEC > RUS > ANT > HAM | PIA > VER > LEC > NOR > RUS > ANT > HAM |
| Singapore Grand Prix | NOR > PIA > VER > LEC > RUS > ANT > HAM > TSU | NOR > HAM > ANT > RUS > VER > PIA > LEC > TSU | RUS > VER > NOR > PIA > ANT > LEC > HAM > TSU |
| Spanish Grand Prix | NOR > PIA > VER > LEC > HAM > RUS > ANT > TSU | VER > NOR > PIA > RUS > LEC > HAM > ANT > TSU | PIA > NOR > LEC > RUS > VER > ANT > HAM > TSU |
| São Paulo Grand Prix | VER > NOR > PIA > RUS > ANT > HAM > TSU | NOR > PIA > VER > ANT > RUS > TSU > HAM | NOR > ANT > VER > RUS > PIA > TSU > HAM |
| United States Grand Prix | VER > NOR > LEC > PIA > HAM > RUS > ANT > TSU | NOR > VER > LEC > HAM > PIA > RUS > ANT > TSU | VER > NOR > LEC > HAM > PIA > RUS > TSU > ANT |

## 每队理论上限

| 分站 | 车队 | 融合 advantage_z p50 | CI80 | Q p50 | R p50 | 执行校正 R p50 |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| Abu Dhabi Grand Prix | McLaren | 0.713 | [-0.074, 1.502] | 0.969 | 0.296 | 0.072 |
| Abu Dhabi Grand Prix | Red Bull Racing | 0.204 | [-0.580, 0.919] | 0.313 | -0.024 | 0.185 |
| Abu Dhabi Grand Prix | Ferrari | -0.281 | [-1.111, 0.485] | -0.631 | 0.379 | 0.151 |
| Abu Dhabi Grand Prix | Mercedes | -0.005 | [-0.885, 0.834] | 0.054 | -0.081 | 0.189 |
| Australian Grand Prix | McLaren | 1.209 | [0.184, 2.954] | 1.327 | 1.051 | 0.000 |
| Australian Grand Prix | Red Bull Racing | -0.113 | [-1.118, 1.695] | -0.946 | 1.264 | 0.000 |
| Australian Grand Prix | Ferrari | -0.308 | [-1.828, 1.456] | 0.010 | -0.804 | 0.000 |
| Australian Grand Prix | Mercedes | 0.548 | [-5.266, 1.802] | 1.515 | -1.201 | 0.000 |
| Austrian Grand Prix | McLaren | 1.160 | [-0.067, 2.055] | 1.193 | 1.141 | 0.300 |
| Austrian Grand Prix | Red Bull Racing | -0.856 | [-1.462, -0.238] | -1.248 | -0.177 | 0.173 |
| Austrian Grand Prix | Ferrari | -0.065 | [-1.041, 0.950] | -0.186 | 0.178 | 0.079 |
| Austrian Grand Prix | Mercedes | 0.356 | [-0.391, 1.090] | 0.731 | -0.407 | 0.224 |
| Azerbaijan Grand Prix | McLaren | -0.190 | [-1.450, 1.424] | -0.253 | -0.069 | 0.033 |
| Azerbaijan Grand Prix | Red Bull Racing | 0.956 | [-0.948, 3.170] | 1.392 | 0.165 | 0.068 |
| Azerbaijan Grand Prix | Ferrari | -0.049 | [-5.375, 1.384] | -0.040 | -0.044 | 0.114 |
| Azerbaijan Grand Prix | Mercedes | 0.693 | [-1.893, 2.680] | 0.968 | 0.230 | 0.070 |
| Bahrain Grand Prix | McLaren | 0.558 | [0.085, 1.096] | 0.715 | 0.269 | 0.167 |
| Bahrain Grand Prix | Red Bull Racing | -0.347 | [-0.987, 0.244] | -0.488 | 0.008 | 0.127 |
| Bahrain Grand Prix | Ferrari | -0.093 | [-0.640, 0.536] | -0.156 | 0.007 | 0.125 |
| Bahrain Grand Prix | Mercedes | 0.459 | [-0.357, 1.323] | 0.524 | 0.302 | 0.203 |
| Belgian Grand Prix | McLaren | 1.287 | [0.283, 2.287] | 1.846 | 0.237 | 0.035 |
| Belgian Grand Prix | Red Bull Racing | 0.237 | [-0.447, 0.946] | 0.407 | -0.048 | 0.057 |
| Belgian Grand Prix | Ferrari | -0.416 | [-0.961, 0.095] | -0.726 | 0.132 | 0.105 |
| Belgian Grand Prix | Mercedes | -0.750 | [-1.313, -0.248] | -1.176 | 0.053 | 0.210 |
| British Grand Prix | McLaren | 0.249 | [-0.738, 1.188] | -0.041 | 0.806 | 0.108 |
| British Grand Prix | Red Bull Racing | 0.957 | [-0.134, 2.071] | 1.557 | -0.155 | 0.030 |
| British Grand Prix | Ferrari | -1.218 | [-2.260, 0.058] | -1.897 | 0.016 | 0.027 |
| British Grand Prix | Mercedes | 0.492 | [-0.572, 1.440] | 0.962 | -0.337 | 0.023 |
| Canadian Grand Prix | McLaren | 0.447 | [-0.537, 1.590] | 0.380 | 0.495 | 0.081 |
| Canadian Grand Prix | Red Bull Racing | 1.111 | [-0.066, 2.156] | 1.791 | -0.210 | 0.239 |
| Canadian Grand Prix | Ferrari | -1.961 | [-4.683, -0.767] | -2.915 | -0.257 | 0.062 |
| Canadian Grand Prix | Mercedes | 1.405 | [0.094, 2.588] | 1.872 | 0.578 | 0.262 |
| Chinese Grand Prix | McLaren | -0.519 | [-1.622, 1.235] | -0.926 | 0.215 | 0.069 |
| Chinese Grand Prix | Red Bull Racing | -0.118 | [-1.063, 1.213] | -0.169 | -0.025 | 0.169 |
| Chinese Grand Prix | Ferrari | -1.001 | [-2.271, 0.484] | -1.662 | 0.192 | 0.092 |
| Chinese Grand Prix | Mercedes | 1.923 | [0.461, 3.213] | 2.926 | 0.151 | 0.212 |
| Dutch Grand Prix | McLaren | 1.444 | [0.408, 2.376] | 1.654 | 1.028 | 0.300 |
| Dutch Grand Prix | Red Bull Racing | 0.201 | [-0.791, 1.481] | 0.445 | -0.169 | 0.286 |
| Dutch Grand Prix | Ferrari | -0.376 | [-1.341, 0.533] | -0.513 | -0.160 | 0.143 |
| Dutch Grand Prix | Mercedes | -0.618 | [-1.431, 0.359] | -1.105 | 0.256 | 0.256 |
| Emilia Romagna Grand Prix | McLaren | 0.092 | [-1.324, 1.233] | -0.158 | 0.447 | 0.153 |
| Emilia Romagna Grand Prix | Red Bull Racing | 0.814 | [-1.506, 1.833] | 1.170 | 0.190 | 0.269 |
| Emilia Romagna Grand Prix | Ferrari | 0.721 | [-0.405, 1.720] | 0.868 | 0.405 | 0.228 |
| Emilia Romagna Grand Prix | Mercedes | -0.603 | [-1.575, 0.409] | -0.810 | -0.192 | 0.187 |
| Hungarian Grand Prix | McLaren | 1.009 | [-0.095, 2.023] | 1.220 | 0.616 | 0.105 |
| Hungarian Grand Prix | Red Bull Racing | -0.788 | [-1.453, 0.006] | -1.106 | -0.260 | 0.181 |
| Hungarian Grand Prix | Ferrari | -0.375 | [-1.083, 0.502] | -0.599 | 0.047 | 0.103 |
| Hungarian Grand Prix | Mercedes | 0.556 | [-0.373, 1.483] | 0.746 | 0.216 | 0.238 |
| Italian Grand Prix | McLaren | -0.367 | [-1.586, 0.750] | -0.729 | 0.292 | 0.069 |
| Italian Grand Prix | Red Bull Racing | 0.118 | [-1.064, 1.245] | 0.155 | 0.055 | 0.206 |
| Italian Grand Prix | Ferrari | 0.417 | [-1.031, 1.521] | 0.651 | 0.014 | 0.073 |
| Italian Grand Prix | Mercedes | 0.395 | [-1.212, 2.452] | 0.590 | 0.068 | 0.087 |
| Japanese Grand Prix | McLaren | 0.517 | [-0.888, 1.880] | 0.548 | 0.512 | 0.138 |
| Japanese Grand Prix | Red Bull Racing | 0.283 | [-0.708, 1.438] | 0.473 | -0.024 | 0.083 |
| Japanese Grand Prix | Ferrari | -0.220 | [-1.486, 0.839] | -0.248 | -0.150 | 0.167 |
| Japanese Grand Prix | Mercedes | 0.029 | [-1.125, 1.066] | -0.065 | 0.199 | 0.169 |
| Las Vegas Grand Prix | McLaren | 0.227 | [0.158, 0.297] | — | 0.227 | 0.051 |
| Las Vegas Grand Prix | Red Bull Racing | -0.179 | [-0.250, -0.106] | — | -0.179 | 0.115 |
| Las Vegas Grand Prix | Ferrari | 0.152 | [0.057, 0.258] | — | 0.152 | 0.162 |
| Las Vegas Grand Prix | Mercedes | 0.186 | [0.119, 0.287] | — | 0.186 | 0.079 |
| Mexico City Grand Prix | McLaren | 0.381 | [-0.852, 1.509] | 0.428 | 0.276 | 0.130 |
| Mexico City Grand Prix | Red Bull Racing | -0.388 | [-1.153, 0.859] | -0.548 | -0.132 | 0.206 |
| Mexico City Grand Prix | Ferrari | 0.215 | [-0.907, 1.452] | 0.270 | 0.084 | 0.229 |
| Mexico City Grand Prix | Mercedes | 0.228 | [-0.662, 1.253] | 0.035 | 0.549 | 0.242 |
| Miami Grand Prix | McLaren | 0.365 | [-0.914, 2.224] | 0.290 | 0.493 | 0.080 |
| Miami Grand Prix | Red Bull Racing | -0.393 | [-1.527, 0.841] | -0.438 | -0.210 | 0.186 |
| Miami Grand Prix | Ferrari | 1.069 | [-0.355, 2.276] | 1.393 | 0.392 | 0.160 |
| Miami Grand Prix | Mercedes | -0.606 | [-1.625, 0.678] | -0.898 | -0.045 | 0.247 |
| Monaco Grand Prix | McLaren | 0.600 | [-0.714, 2.026] | 0.440 | 0.909 | 0.140 |
| Monaco Grand Prix | Red Bull Racing | 0.547 | [-0.732, 1.917] | 1.074 | -0.361 | 0.134 |
| Monaco Grand Prix | Ferrari | 0.028 | [-1.200, 1.683] | -0.380 | 0.699 | 0.111 |
| Monaco Grand Prix | Mercedes | -0.292 | [-3.083, 0.949] | -0.167 | -0.545 | 0.300 |
| Qatar Grand Prix | McLaren | 0.557 | [-0.206, 2.151] | 0.467 | 0.738 | 0.263 |
| Qatar Grand Prix | Red Bull Racing | 1.090 | [-0.727, 2.357] | 1.534 | 0.268 | 0.160 |
| Qatar Grand Prix | Ferrari | -0.500 | [-1.342, 0.866] | -0.444 | -0.653 | 0.126 |
| Qatar Grand Prix | Mercedes | -0.321 | [-1.768, 0.974] | -0.729 | 0.389 | 0.191 |
| Saudi Arabian Grand Prix | McLaren | 0.347 | [-1.240, 1.884] | 0.252 | 0.464 | 0.180 |
| Saudi Arabian Grand Prix | Red Bull Racing | 0.240 | [-0.778, 1.680] | 0.428 | -0.118 | 0.097 |
| Saudi Arabian Grand Prix | Ferrari | 0.331 | [-0.751, 1.497] | 0.526 | 0.017 | 0.150 |
| Saudi Arabian Grand Prix | Mercedes | -0.309 | [-1.253, 0.876] | -0.578 | 0.169 | 0.140 |
| Singapore Grand Prix | McLaren | 0.103 | [-1.440, 1.344] | 0.101 | 0.121 | 0.045 |
| Singapore Grand Prix | Red Bull Racing | -0.391 | [-1.241, 0.980] | -0.461 | -0.193 | 0.058 |
| Singapore Grand Prix | Ferrari | 0.845 | [-0.583, 2.088] | 1.122 | 0.207 | 0.300 |
| Singapore Grand Prix | Mercedes | 0.030 | [-0.996, 1.190] | -0.151 | 0.367 | 0.119 |
| Spanish Grand Prix | McLaren | 0.694 | [0.028, 1.669] | 0.451 | 1.111 | 0.300 |
| Spanish Grand Prix | Red Bull Racing | -0.263 | [-0.812, 0.131] | -0.494 | 0.180 | 0.300 |
| Spanish Grand Prix | Ferrari | -0.406 | [-0.842, 0.067] | -0.685 | 0.113 | 0.300 |
| Spanish Grand Prix | Mercedes | 0.597 | [-0.083, 1.365] | 1.076 | -0.250 | 0.300 |
| São Paulo Grand Prix | McLaren | 0.687 | [-0.639, 3.177] | 0.902 | 0.310 | 0.279 |
| São Paulo Grand Prix | Red Bull Racing | 0.721 | [-7.171, 1.952] | 0.945 | 0.269 | 0.271 |
| São Paulo Grand Prix | Ferrari | 0.034 | [-1.183, 3.117] | 0.181 | -0.219 | 0.114 |
| São Paulo Grand Prix | Mercedes | -0.315 | [-0.993, 2.494] | -0.762 | 0.525 | 0.300 |
| United States Grand Prix | McLaren | 0.439 | [-0.520, 1.294] | 0.598 | 0.131 | 0.111 |
| United States Grand Prix | Red Bull Racing | 0.188 | [-0.862, 1.192] | 0.224 | 0.090 | 0.251 |
| United States Grand Prix | Ferrari | 0.306 | [-0.961, 1.745] | 0.362 | 0.215 | 0.123 |
| United States Grand Prix | Mercedes | -0.274 | [-1.228, 0.648] | -0.565 | 0.280 | 0.298 |

## 配速与赛果代理不一致（排名差 ≥ 2）

| 分站 | 车手 | 车队 | 配速排名 | classification proxy | 差值 |
| --- | --- | --- | ---: | ---: | ---: |
| Abu Dhabi Grand Prix | LEC | Ferrari | 1 | 4 | +3 |
| Abu Dhabi Grand Prix | PIA | McLaren | 4 | 2 | -2 |
| Abu Dhabi Grand Prix | VER | Red Bull Racing | 3 | 1 | -2 |
| Australian Grand Prix | ANT | Mercedes | 8 | 4 | -4 |
| Australian Grand Prix | HAM | Ferrari | 5 | 8 | +3 |
| Australian Grand Prix | LAW | Red Bull Racing | 2 | 6 | +4 |
| Australian Grand Prix | LEC | Ferrari | 7 | 5 | -2 |
| Australian Grand Prix | PIA | McLaren | 4 | 7 | +3 |
| Australian Grand Prix | RUS | Mercedes | 6 | 3 | -3 |
| Azerbaijan Grand Prix | LEC | Ferrari | 5 | 7 | +2 |
| Azerbaijan Grand Prix | NOR | McLaren | 3 | 5 | +2 |
| Azerbaijan Grand Prix | TSU | Red Bull Racing | 6 | 4 | -2 |
| Bahrain Grand Prix | ANT | Mercedes | 5 | 8 | +3 |
| Bahrain Grand Prix | NOR | McLaren | 1 | 3 | +2 |
| Bahrain Grand Prix | RUS | Mercedes | 7 | 2 | -5 |
| Bahrain Grand Prix | VER | Red Bull Racing | 4 | 6 | +2 |
| Belgian Grand Prix | ANT | Mercedes | 6 | 8 | +2 |
| Belgian Grand Prix | RUS | Mercedes | 7 | 5 | -2 |
| Canadian Grand Prix | ANT | Mercedes | 1 | 3 | +2 |
| Canadian Grand Prix | NOR | McLaren | 4 | 6 | +2 |
| Canadian Grand Prix | RUS | Mercedes | 5 | 1 | -4 |
| Chinese Grand Prix | HAM | Ferrari | 4 | 6 | +2 |
| Chinese Grand Prix | PIA | McLaren | 3 | 1 | -2 |
| Chinese Grand Prix | RUS | Mercedes | 5 | 3 | -2 |
| Chinese Grand Prix | VER | Red Bull Racing | 2 | 4 | +2 |
| Dutch Grand Prix | HAM | Ferrari | 5 | 7 | +2 |
| Dutch Grand Prix | RUS | Mercedes | 7 | 4 | -3 |
| Emilia Romagna Grand Prix | VER | Red Bull Racing | 3 | 1 | -2 |
| Japanese Grand Prix | LEC | Ferrari | 6 | 4 | -2 |
| Japanese Grand Prix | PIA | McLaren | 1 | 3 | +2 |
| Japanese Grand Prix | RUS | Mercedes | 3 | 5 | +2 |
| Japanese Grand Prix | VER | Red Bull Racing | 4 | 1 | -3 |
| Las Vegas Grand Prix | ANT | Mercedes | 1 | 4 | +3 |
| Las Vegas Grand Prix | LEC | Ferrari | 4 | 6 | +2 |
| Las Vegas Grand Prix | RUS | Mercedes | 6 | 3 | -3 |
| Mexico City Grand Prix | ANT | Mercedes | 3 | 5 | +2 |
| Mexico City Grand Prix | LEC | Ferrari | 7 | 2 | -5 |
| Mexico City Grand Prix | NOR | McLaren | 4 | 1 | -3 |
| Mexico City Grand Prix | PIA | McLaren | 1 | 4 | +3 |
| Mexico City Grand Prix | RUS | Mercedes | 2 | 6 | +4 |
| Mexico City Grand Prix | VER | Red Bull Racing | 5 | 3 | -2 |
| Miami Grand Prix | ANT | Mercedes | 7 | 5 | -2 |
| Miami Grand Prix | LEC | Ferrari | 4 | 6 | +2 |
| Miami Grand Prix | RUS | Mercedes | 5 | 3 | -2 |
| Monaco Grand Prix | NOR | McLaren | 4 | 1 | -3 |
| Qatar Grand Prix | VER | Red Bull Racing | 3 | 1 | -2 |
| Singapore Grand Prix | ANT | Mercedes | 3 | 5 | +2 |
| Singapore Grand Prix | HAM | Ferrari | 2 | 7 | +5 |
| Singapore Grand Prix | NOR | McLaren | 1 | 3 | +2 |
| Singapore Grand Prix | PIA | McLaren | 6 | 4 | -2 |
| Singapore Grand Prix | RUS | Mercedes | 4 | 1 | -3 |
| Singapore Grand Prix | VER | Red Bull Racing | 5 | 2 | -3 |
| Spanish Grand Prix | LEC | Ferrari | 5 | 3 | -2 |
| Spanish Grand Prix | PIA | McLaren | 3 | 1 | -2 |
| Spanish Grand Prix | VER | Red Bull Racing | 1 | 5 | +4 |
| São Paulo Grand Prix | ANT | Mercedes | 4 | 2 | -2 |
| São Paulo Grand Prix | PIA | McLaren | 2 | 6 | +4 |

## 解释边界

- classification proxy 来自 v5 本地代理分类，不等同于经赛后处罚修订的 FIA 最终分类。
- 配速与赛果代理不一致只是一张待查清单；不能从差值直接断言策略、可靠性、事故或处罚中的哪一项造成结果。
- 同调教指 parc-fermé 周末族代理，不表示已观测到两位车手采用完全相同设置。
