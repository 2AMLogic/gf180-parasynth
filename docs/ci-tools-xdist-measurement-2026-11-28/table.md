| candidate | repeat | runner | job wall (min) | pytest step (min, API) | pytest's own (s) | CPU user+sys (s) | CPU / wall | peak tree RSS (MB) | max processes | pytest's summary line | collection union |
|---|---|---|---|---|---|---|---|---|---|---|---|
| n1 | 1 | GitHub Actions 1000679035 | 85.8 | 85.0 | 5101.14 | 6699 | 1.31 | 1485 | 8 | 6649 passed, 27 skipped, 2 xfailed in 5101.14s (1:25:01) | 6678 ran once; outcomes identical to baseline |
| n1 | 2 | GitHub Actions 1000679038 | 50.2 | 49.6 | 2975.45 | 4110 | 1.38 | 1461 | 8 | 6649 passed, 27 skipped, 2 xfailed in 2975.45s (0:49:35) | 6678 ran once; outcomes identical to baseline |
| n1 | 3 | GitHub Actions 1000679036 | 65.2 | 64.7 | 3876.44 | 5172 | 1.33 | 1490 | 8 | 6649 passed, 27 skipped, 2 xfailed in 3876.44s (1:04:36) | 6678 ran once; outcomes identical to baseline |
| n2 | 1 | GitHub Actions 1000679042 | 48.8 | 48.1 | 2886.71 | 7068 | 2.45 | 1811 | 12 | 6649 passed, 27 skipped, 2 xfailed in 2886.71s (0:48:06) | 6678 ran once; outcomes identical to baseline |
| n2 | 2 | GitHub Actions 1000679039 | 32.9 | 32.4 | 1942.66 | 4892 | 2.52 | 1960 | 12 | 6649 passed, 27 skipped, 2 xfailed in 1942.66s (0:32:22) | 6678 ran once; outcomes identical to baseline |
| n2 | 3 | GitHub Actions 1000679037 | 48.2 | 47.6 | 2857.87 | 7004 | 2.45 | 1808 | 12 | 6649 passed, 27 skipped, 2 xfailed in 2857.87s (0:47:37) | 6678 ran once; outcomes identical to baseline |
| nauto | 1 | GitHub Actions 1000679032 | 26.6 | 26.1 | 1567.51 | 5585 | 3.56 | 2668 | 18 | 6642 passed, 34 skipped, 2 xfailed in 1567.51s (0:26:07) | 6678 ran once; 7 outcome differences vs baseline |
| nauto | 2 | GitHub Actions 1000679033 | 45.3 | 44.7 | 2683.11 | 9404 | 3.50 | 2725 | 14 | 6642 passed, 34 skipped, 2 xfailed in 2683.11s (0:44:43) | 6678 ran once; 7 outcome differences vs baseline |
| nauto | 3 | GitHub Actions 1000679034 | 32.9 | 32.2 | 1934.22 | 7009 | 3.62 | 2820 | 18 | 6642 passed, 34 skipped, 2 xfailed in 1934.22s (0:32:14) | 6678 ran once; 7 outcome differences vs baseline |
| n8 | 1 | GitHub Actions 1000679041 | 47.5 | 46.9 | 2812.99 | 10042 | 3.57 | 4172 | 18 | 6 failed, 6636 passed, 34 skipped, 2 xfailed in 2812.99s (0:46:52) | 6678 ran once; 13 outcome differences vs baseline |
| n8 | 2 | GitHub Actions 1000679040 | 33.7 | 33.1 | 1986.44 | 7277 | 3.66 | 4689 | 23 | 4 failed, 6638 passed, 34 skipped, 2 xfailed in 1986.44s (0:33:06) | 6678 ran once; 11 outcome differences vs baseline |
| n8 | 3 | GitHub Actions 1000679031 | 24.9 | 24.4 | 1466.55 | 5457 | 3.72 | 4566 | 26 | 6642 passed, 34 skipped, 2 xfailed in 1466.55s (0:24:26) | 6678 ran once; 7 outcome differences vs baseline |
| s0 | 1 | GitHub Actions 1000680375 | 45.2 | 44.5 | 2669.55 | 3273 | 1.23 | 919 | 8 | 3211 passed, 20 skipped, 2 xfailed in 2669.55s (0:44:29) | 6678 ran once; outcomes identical to baseline |
| s0 | 2 | GitHub Actions 1000680372 | 33.9 | 33.3 | 1998.88 | 2573 | 1.29 | 927 | 8 | 3211 passed, 20 skipped, 2 xfailed in 1998.88s (0:33:18) | 6678 ran once; outcomes identical to baseline |
| s0 | 3 | GitHub Actions 1000680370 | 44.9 | 44.2 | 2649.46 | 3253 | 1.23 | 936 | 8 | 3211 passed, 20 skipped, 2 xfailed in 2649.46s (0:44:09) | 6678 ran once; outcomes identical to baseline |
| s1 | 1 | GitHub Actions 1000680374 | 45.0 | 44.3 | 2657.37 | 3669 | 1.38 | 1225 | 6 | 3438 passed, 7 skipped in 2657.37s (0:44:17) | 6678 ran once; outcomes identical to baseline |
| s1 | 2 | GitHub Actions 1000680373 | 33.6 | 32.9 | 1974.81 | 2761 | 1.40 | 1181 | 6 | 3438 passed, 7 skipped in 1974.81s (0:32:54) | 6678 ran once; outcomes identical to baseline |
| s1 | 3 | GitHub Actions 1000680371 | 43.6 | 43.0 | 2576.16 | 3611 | 1.40 | 1193 | 6 | 3438 passed, 7 skipped in 2576.16s (0:42:56) | 6678 ran once; outcomes identical to baseline |

| candidate | runs | job wall min | job wall median | job wall max | pytest's own median (s) | CPU / wall median | peak tree RSS max (MB) |
|---|---|---|---|---|---|---|---|
| n1 | 3 | 50.2 | 65.2 | 85.8 | 3876 | 1.33 | 1490 |
| n2 | 3 | 32.9 | 48.2 | 48.8 | 2858 | 2.45 | 1960 |
| nauto | 3 | 26.6 | 32.9 | 45.3 | 1934 | 3.56 | 2820 |
| n8 | 3 | 24.9 | 33.7 | 47.5 | 1986 | 3.66 | 4689 |
| s0 | 3 | 33.9 | 44.9 | 45.2 | 2649 | 1.23 | 936 |
| s1 | 3 | 33.6 | 43.6 | 45.0 | 2576 | 1.40 | 1225 |
