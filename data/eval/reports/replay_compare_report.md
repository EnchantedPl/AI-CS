# Replay Compare Report

- mode: `mix`
- retrieval_only: `True`
- thresholds: nDCG@5 >= `0.005`, MRR >= `0.003`, p95_rt_increase <= `30.0` ms

## Metric Guide

| metric | meaning | direction |
|---|---|---|
| citation_hit@1 | 首条引用是否命中金标文档 | higher better |
| citation_hit@k | 前k条引用是否至少命中一个金标文档 | higher better |
| MRR | 首个命中文档的倒数排名，越靠前越高 | higher better |
| nDCG@3 / nDCG@5 / nDCG@k | 排序质量，兼顾位置折损 | higher better |
| answer_substring_match | 回答与标准答案是否包含匹配 | higher better |
| answer_char_f1 | 基于字符级重叠的回答相似度 | higher better |
| avg_rt_ms / p95_rt_ms | 平均/尾延迟，评估性能代价 | lower better |

## Scenario Summary

| scenario | count | avg_rt_ms | p95_rt_ms | avg_retrieved_count | rerank_applied_ratio | citation_hit@1 | citation_hit@k | MRR | nDCG@3 | nDCG@5 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 200 | 481.22 | 958.6 | 4.96 | 0.0 | 0.94 | 0.94 | 0.94 | 1.16 | 1.23 |
| rerank_on | 200 | 1475.28 | 1574.96 | 1.99 | 1.0 | 0.94 | 0.94 | 0.94 | 1.06 | 1.06 |

## Per Query Delta

| query | base_rt_ms | rerank_rt_ms | delta_rt_ms | base_top_citation | rerank_top_citation |
|---|---:|---:|---:|---|---|
| 退款申请通过后多久到账？ | 2065.0 | 82576.18 | 80511.18 | customer_service_demo.md#51e560d1aa568668 | customer_service_demo.md#51e560d1aa568668 |
| 七天无理由退货有哪些条件？ | 456.63 | 1703.82 | 1247.19 | customer_service_demo.md#51e560d1aa568668 | customer_service_demo.md#51e560d1aa568668 |
| 商品价格和库存在哪里看？ | 538.61 | 1348.11 | 809.5 | public_cmrc_0193.md#ac3c02bed9263d98 | public_cmrc_0193.md#ac3c02bed9263d98 |
| 涉及法律投诉的问题应该怎么处理？ | 839.85 | 1477.27 | 637.42 | public_cmrc_0284.md#fba920d832bac80c | public_cmrc_0280.md#ccc078ebb557546e |
| 范廷颂是什么时候被任为主教的？ | 955.53 | 1237.8 | 282.27 | public_cmrc_0000.md#d7d4b1178541ff4b | public_cmrc_0000.md#d7d4b1178541ff4b |
| 1990年，范廷颂担任什么职务？ | 460.11 | 1050.5 | 590.39 | public_cmrc_0001.md#6e9661c481e2dc09 | public_cmrc_0001.md#6e9661c481e2dc09 |
| 范廷颂是于何时何地出生的？ | 534.34 | 1132.93 | 598.59 | public_cmrc_0002.md#faef6f0b3fb04230 | public_cmrc_0002.md#faef6f0b3fb04230 |
| 1994年3月，范廷颂担任什么职务？ | 325.35 | 1019.85 | 694.5 | public_cmrc_0003.md#7e9b5ea4561ac42b | public_cmrc_0003.md#7e9b5ea4561ac42b |
| 范廷颂是何时去世的？ | 359.55 | 1302.42 | 942.87 | public_cmrc_0004.md#c06e0dcd25cf6020 | public_cmrc_0004.md#c06e0dcd25cf6020 |
| 安雅·罗素法参加了什么比赛获得了亚军？ | 372.84 | 1058.83 | 685.99 | public_cmrc_0005.md#f1fbff6bad5343ac | public_cmrc_0005.md#f1fbff6bad5343ac |
| Russell Tanoue对安雅·罗素法的评价是什么？ | 519.42 | 994.22 | 474.8 | public_cmrc_0006.md#119bd9906e2d2570 | public_cmrc_0006.md#119bd9906e2d2570 |
| 安雅·罗素法合作过的香港杂志有哪些？ | 474.92 | 1305.34 | 830.42 | public_cmrc_0007.md#5ee26d01177275ea | public_cmrc_0007.md#5ee26d01177275ea |
| 毕业后的安雅·罗素法职业是什么？ | 225.55 | 1499.59 | 1274.04 | public_cmrc_0008.md#7627525170c9c924 | public_cmrc_0008.md#7627525170c9c924 |
| 岬太郎在第一次南葛市生活时的搭档是谁？ | 396.89 | 1327.38 | 930.49 | public_cmrc_0009.md#c386a340c922cf84 | public_cmrc_0009.md#c386a340c922cf84 |
| 日本队夺得世青冠军，岬太郎发挥了什么作用？ | 283.32 | 1193.79 | 910.47 | public_cmrc_0010.md#6764437def085223 | public_cmrc_0010.md#6764437def085223 |
| 岬太郎与谁一起组成了「3M」组合？ | 222.82 | 1179.51 | 956.69 | public_cmrc_0011.md#68dfb2122167fd8a | public_cmrc_0011.md#68dfb2122167fd8a |
| NGC 6231的经纬度是多少？ | 494.86 | 1014.07 | 519.21 | public_cmrc_0012.md#ded7d4a8d089d9ee | public_cmrc_0012.md#ded7d4a8d089d9ee |
| NGC 6231的年龄是多少？ | 498.99 | 1072.44 | 573.45 | public_cmrc_0013.md#1e256dd4fca73d0f | public_cmrc_0013.md#1e256dd4fca73d0f |
| NGC 6231星团内最亮的星是哪颗星？ | 741.12 | 835.4 | 94.28 | public_cmrc_0014.md#b3e86d0a7f2c8d27 | public_cmrc_0014.md#b3e86d0a7f2c8d27 |
| NGC 6231被首次记录在星表中的名字是什么？ | 536.35 | 834.51 | 298.16 | public_cmrc_0015.md#cf244d1d24fb786f | public_cmrc_0015.md#cf244d1d24fb786f |
| NGC 6231分别被谁独立发现过？ | 537.09 | 1065.84 | 528.75 | public_cmrc_0016.md#c045aa6290947a90 | public_cmrc_0016.md#c045aa6290947a90 |
| 国际初中科学奥林匹克的参赛对象是谁？ | 345.85 | 1080.94 | 735.09 | public_cmrc_0017.md#91b7858f47922836 | public_cmrc_0017.md#91b7858f47922836 |
| 次比赛最早什么时候开始举办？ | 338.94 | 1030.85 | 691.91 | public_cmrc_0018.md#ded6cf34aa498cfe | public_cmrc_0018.md#ded6cf34aa498cfe |
| 试验系考试科目有哪些？ | 615.3 | 1082.03 | 466.73 | public_cmrc_0019.md#71bc46902b4b6f0f | public_cmrc_0019.md#71bc46902b4b6f0f |
| 实验系考试最多可以几人一组？ | 376.49 | 1228.38 | 851.89 | public_cmrc_0020.md#1290f88213f9d639 | public_cmrc_0020.md#1290f88213f9d639 |
| 从第一届到第四届，实验系考试的题数发生了什么变化？ | 305.03 | 939.05 | 634.02 | public_cmrc_0021.md#e280b75495346ef1 | public_cmrc_0021.md#e280b75495346ef1 |
| 江苏路街道在上海市的什么地方？ | 216.05 | 1588.51 | 1372.46 | public_cmrc_0022.md#7f3bfeb4e108c27a | public_cmrc_0022.md#7f3bfeb4e108c27a |
| 江苏路街道下辖多少个居委会？ | 235.27 | 1395.37 | 1160.1 | public_cmrc_0023.md#bb2d84fcf05acdcd | public_cmrc_0023.md#bb2d84fcf05acdcd |
| 江苏路街道较著名的近代建筑有什么？ | 184.96 | 884.36 | 699.4 | public_cmrc_0024.md#c616c6dc11b8422a | public_cmrc_0024.md#c616c6dc11b8422a |
| 黄独分布在哪些地区？ | 223.25 | 1001.47 | 778.22 | public_cmrc_0025.md#95b301da6b34abb2 | public_cmrc_0025.md#95b301da6b34abb2 |
| 黄独生长的地区海拔约是多少米？ | 323.33 | 1139.65 | 816.32 | public_cmrc_0026.md#b5626f9ae5781132 | public_cmrc_0026.md#b5626f9ae5781132 |
| 黄独的英文名是什么？ | 222.71 | 1179.29 | 956.58 | public_cmrc_0027.md#6abd8724ff681060 | public_cmrc_0027.md#6abd8724ff681060 |
| 什么是黄独？ | 345.8 | 1411.4 | 1065.6 | public_cmrc_0028.md#a25de9208d45a325 | public_cmrc_0028.md#a25de9208d45a325 |
| 黄独的外皮是什么颜色的？ | 332.17 | 1223.3 | 891.13 | public_cmrc_0029.md#764d58dd2f837d97 | public_cmrc_0029.md#764d58dd2f837d97 |
| 什么是烯酮？ | 242.62 | 1197.91 | 955.29 | public_cmrc_0030.md#61b0ab2bad950c6d | public_cmrc_0030.md#61b0ab2bad950c6d |
| 在烯酮研究方面作了很大贡献的人是谁？ | 423.6 | 1698.32 | 1274.72 | public_cmrc_0031.md#f8dc851744f79c37 | public_cmrc_0031.md#f8dc851744f79c37 |
| 什么是乙烯酮？ | 281.04 | 1187.67 | 906.63 | public_cmrc_0032.md#31bd0c745daf2d07 | public_cmrc_0032.md#31bd0c745daf2d07 |
| 工业上是怎样合成乙酸酐的？ | 307.11 | 915.98 | 608.87 | public_cmrc_0033.md#7b340241ebc211da | public_cmrc_0033.md#7b340241ebc211da |
| 烯酮的作用有哪些？ | 417.09 | 1192.31 | 775.22 | public_cmrc_0034.md#e158ea2f978fc89f | public_cmrc_0034.md#e158ea2f978fc89f |
| 墨西哥土拨鼠又被称之为什么？ | 373.33 | 1240.14 | 866.81 | public_cmrc_0035.md#1426bbbc9857e80a | public_cmrc_0035.md#1426bbbc9857e80a |
| 墨西哥土拨鼠栖息的地区海拔约是多少米？ | 408.36 | 1708.67 | 1300.31 | public_cmrc_0036.md#67c87c9b9795f475 | public_cmrc_0036.md#67c87c9b9795f475 |
| 墨西哥土拨鼠主要分布在哪些地区？ | 628.6 | 1024.49 | 395.89 | public_cmrc_0037.md#ebbc19c4796b37e1 | public_cmrc_0037.md#ebbc19c4796b37e1 |
| 他们的天敌是谁？ | 570.61 | 1233.96 | 663.35 | public_cmrc_0038.md#fc08e8a2d1864d7f | public_cmrc_0038.md#fc08e8a2d1864d7f |
| 墨西哥土拨鼠何时达至性成熟？ | 588.79 | 1104.87 | 516.08 | public_cmrc_0039.md#a8e19b1a68bb7ad3 | public_cmrc_0039.md#a8e19b1a68bb7ad3 |
| 2014年初，王栋喜获什么？ | 443.75 | 1432.34 | 988.59 | public_cmrc_0040.md#fa46853746fb1669 | public_cmrc_0040.md#fa46853746fb1669 |
| 王栋2009年中超夺冠的奖金为什么被扣发？ | 564.06 | 1310.19 | 746.13 | public_cmrc_0041.md#6afb27d8cf0b4408 | public_cmrc_0041.md#6afb27d8cf0b4408 |
| 王栋为什么戴着保护面具参加了深圳红钻的数场保级战役？ | 731.98 | 1144.99 | 413.01 | public_cmrc_0042.md#3f2cb867363d03f3 | public_cmrc_0042.md#3f2cb867363d03f3 |
| 王栋为什么急需用钱？ | 503.72 | 1101.17 | 597.45 | public_cmrc_0043.md#c0d3b710c4b77eb9 | public_cmrc_0043.md#c0d3b710c4b77eb9 |
| 米尼科伊岛（Minicoy）位于什么地方？ | 587.78 | 1071.63 | 483.85 | public_cmrc_0044.md#33f2bebcc1796322 | public_cmrc_0044.md#33f2bebcc1796322 |
| 米尼科伊岛附近有什么海峡或礁石？ | 363.88 | 955.24 | 591.36 | public_cmrc_0045.md#d3386161ddb1dad5 | public_cmrc_0045.md#d3386161ddb1dad5 |
| 截止2001年岛上的人口有多少？ | 426.04 | 1192.76 | 766.72 | public_cmrc_0046.md#ca5762c3bca75fdf | public_cmrc_0046.md#ca5762c3bca75fdf |
| 岛上除了椰子树，唯一的地标是什么？ | 434.2 | 1155.02 | 720.82 | public_cmrc_0047.md#04c154fd49ff7438 | public_cmrc_0047.md#04c154fd49ff7438 |
| Viringili曾被用来作为什么用途？ | 182.33 | 1022.09 | 839.76 | public_cmrc_0048.md#46a3a8232b88a20f | public_cmrc_0048.md#46a3a8232b88a20f |
| 中国是什么时候参加奥运会摔跤项目的？ | 188.32 | 1186.2 | 997.88 | public_cmrc_0049.md#b62b38732f3f3917 | public_cmrc_0049.md#b62b38732f3f3917 |
| 中国什么时候获得奥运会摔跤首金？ | 199.95 | 1578.93 | 1378.98 | public_cmrc_0050.md#b23a805697836461 | public_cmrc_0050.md#b23a805697836461 |
| 参加2008年北京奥运会的中国摔跤队有多少运动员参赛？ | 356.05 | 951.99 | 595.94 | public_cmrc_0051.md#c2129e5ce449c13a | public_cmrc_0051.md#c2129e5ce449c13a |
| 华阳路街道四周相连的是什么地方？ | 296.25 | 1313.04 | 1016.79 | public_cmrc_0052.md#e0f80dc93d253924 | public_cmrc_0052.md#e0f80dc93d253924 |
| 华阳路街道下辖多少个居委会？ | 347.68 | 1154.66 | 806.98 | public_cmrc_0053.md#d3c17a9e938db785 | public_cmrc_0053.md#d3c17a9e938db785 |
| 愚园路历史文化风貌区的重要组成部分是什么？ | 343.78 | 1339.02 | 995.24 | public_cmrc_0054.md#2f160a4637a89e61 | public_cmrc_0054.md#2f160a4637a89e61 |
| 华阳路街道途经哪些轨道交通线路？ | 405.11 | 839.79 | 434.68 | public_cmrc_0055.md#fa88dac52cbc30c3 | public_cmrc_0055.md#fa88dac52cbc30c3 |
| 冯素弗是何时去世的？ | 607.81 | 771.57 | 163.76 | public_cmrc_0056.md#f0bf73c419c0c512 | public_cmrc_0056.md#f0bf73c419c0c512 |
| 谁首次提出了四重键的概念？ | 402.68 | 989.98 | 587.3 | public_cmrc_0057.md#0c5355b1ff4c4dad | public_cmrc_0057.md#0c5355b1ff4c4dad |
| 水腺毛草的学名是什么？ | 720.65 | 878.29 | 157.64 | public_cmrc_0058.md#fcb6a13d76186272 | public_cmrc_0058.md#fcb6a13d76186272 |
| 水腺毛草通常被称之为什么？ | 220.18 | 946.05 | 725.87 | public_cmrc_0059.md#0fcd19cd512b6afb | public_cmrc_0059.md#0fcd19cd512b6afb |
| 水腺毛草生长的状态是怎样的？ | 398.6 | 1060.28 | 661.68 | public_cmrc_0060.md#b74830a096aff248 | public_cmrc_0060.md#b74830a096aff248 |
| 水腺毛草首次采得是什么时候？ | 490.6 | 995.79 | 505.19 | public_cmrc_0061.md#353c8ac9778222db | public_cmrc_0061.md#353c8ac9778222db |
| 1996年扬·弗利兹提出了什么意见？ | 555.37 | 1045.32 | 489.95 | public_cmrc_0062.md#fc77f32e42d228f2 | public_cmrc_0062.md#fc77f32e42d228f2 |
| 利亚伊奇是哪个国家的足球运动员？ | 677.87 | 1158.21 | 480.34 | public_cmrc_0063.md#5254264f565f0279 | public_cmrc_0063.md#5254264f565f0279 |
| 利亚伊奇目前效力于哪支球队？ | 524.74 | 852.84 | 328.1 | public_cmrc_0064.md#bcbda06b917fc16f | public_cmrc_0064.md#bcbda06b917fc16f |
| 利亚伊奇取得的第一个入球是在哪场赛事上？ | 481.03 | 1039.61 | 558.58 | public_cmrc_0065.md#fe2db948db7e1475 | public_cmrc_0065.md#fe2db948db7e1475 |
| 2012年意甲联赛后，德里奥·罗西为什么被俱乐部解雇了？ | 443.01 | 1286.87 | 843.86 | public_cmrc_0066.md#9de12e19c9a95040 | public_cmrc_0066.md#9de12e19c9a95040 |
| 2012-13赛季，拿积有哪些杰出功绩？ | 431.26 | 1283.98 | 852.72 | public_cmrc_0067.md#ced64b2b2c0f0bb4 | public_cmrc_0067.md#ced64b2b2c0f0bb4 |
| 什么是督邮？ | 534.45 | 965.05 | 430.6 | public_cmrc_0068.md#a7d525b607b0d642 | public_cmrc_0068.md#a7d525b607b0d642 |
| 督邮主要负责什么工作？ | 563.77 | 1054.82 | 491.05 | public_cmrc_0069.md#e034937b378caff1 | public_cmrc_0069.md#e034937b378caff1 |
| 督邮的地位从什么时候开始下降？ | 603.9 | 1025.35 | 421.45 | public_cmrc_0070.md#fb0cb00034c0b9b3 | public_cmrc_0070.md#fb0cb00034c0b9b3 |
| 北齐将督邮改为什么？ | 543.66 | 794.32 | 250.66 | public_cmrc_0071.md#d824ac3cf2f0b589 | public_cmrc_0071.md#d824ac3cf2f0b589 |
| 督邮被废止是在什么时候？ | 275.48 | 897.47 | 621.99 | public_cmrc_0072.md#eec505f51d72c6d4 | public_cmrc_0072.md#eec505f51d72c6d4 |
| 家铉翁是哪里人？ | 300.15 | 1025.85 | 725.7 | public_cmrc_0073.md#1f7c9907377718bd | public_cmrc_0073.md#1f7c9907377718bd |
| 家铉翁先祖什么时候迁居四川的？ | 237.8 | 1284.72 | 1046.92 | public_cmrc_0074.md#9bfbdd11b8922f2f | public_cmrc_0074.md#9bfbdd11b8922f2f |
| 家铉翁赐进士出身是哪一年？ | 236.15 | 1279.33 | 1043.18 | public_cmrc_0075.md#4f19f56d64215bf4 | public_cmrc_0075.md#4f19f56d64215bf4 |
| 皇上赐衣服并放还时，家铉翁多大年龄？ | 399.48 | 1895.77 | 1496.29 | public_cmrc_0076.md#b527bef78803950b | public_cmrc_0076.md#b527bef78803950b |
| 家铉翁著有哪些书目？ | 449.88 | 1290.86 | 840.98 | public_cmrc_0077.md#aa95a88677c7554d | public_cmrc_0077.md#aa95a88677c7554d |
| 《无双大蛇Z》是谁旗下ω-force开发的动作游戏？ | 341.47 | 1138.44 | 796.97 | public_cmrc_0078.md#64012c11cdd0781a | public_cmrc_0078.md#64012c11cdd0781a |
| 《无双大蛇Z》将什么的所有内容合并移植到最新的游戏及平台上？ | 476.96 | 1099.84 | 622.88 | public_cmrc_0079.md#04cb17b0af877ab2 | public_cmrc_0079.md#04cb17b0af877ab2 |
| 《无双大蛇Z》新增了多少个关卡？ | 508.42 | 957.22 | 448.8 | public_cmrc_0080.md#dcd966e4710b2004 | public_cmrc_0080.md#dcd966e4710b2004 |
| 《无双大蛇Z》在什么时候推出了Windows版？ | 483.67 | 1105.21 | 621.54 | public_cmrc_0081.md#90220a45f1e44732 | public_cmrc_0081.md#90220a45f1e44732 |
| 崇庆县属于哪个省？ | 658.69 | 1076.07 | 417.38 | public_cmrc_0082.md#b8ac09ab7ceb5164 | public_cmrc_0082.md#b8ac09ab7ceb5164 |
| 1950年1月12日发生了哪些事件？ | 387.63 | 1171.65 | 784.02 | public_cmrc_0083.md#25d530c0efc12d3b | public_cmrc_0083.md#25d530c0efc12d3b |
| 崇庆县划归哪个市管辖？ | 531.23 | 1260.39 | 729.16 | public_cmrc_0084.md#c260287f8d7bf203 | public_cmrc_0084.md#c260287f8d7bf203 |
| 对崇庆县开始实施军事管制是哪一个事件？ | 360.39 | 1159.06 | 798.67 | public_cmrc_0085.md#e36ba8f6c194faaa | public_cmrc_0085.md#e36ba8f6c194faaa |
| 《花吃了那女孩》的故事地点在哪里？ | 343.83 | 1160.49 | 816.66 | public_cmrc_0086.md#c5a0aee5636f7d9d | public_cmrc_0086.md#c5a0aee5636f7d9d |
| 电影中的旁白由谁担纲负责？ | 486.8 | 910.74 | 423.94 | public_cmrc_0087.md#02c8508a3ad7e327 | public_cmrc_0087.md#02c8508a3ad7e327 |
| 《花吃了那女孩》由几段不同的故事组成？ | 545.86 | 841.77 | 295.91 | public_cmrc_0088.md#be6146b67cf43a2c | public_cmrc_0088.md#be6146b67cf43a2c |
| Spancer与热恋三年的谁分手了？ | 513.05 | 857.98 | 344.93 | public_cmrc_0089.md#048d4075e52fc843 | public_cmrc_0089.md#048d4075e52fc843 |
| 常见的硫氰酸盐包括哪些？ | 389.95 | 980.94 | 590.99 | public_cmrc_0090.md#ecd69a89a5d61919 | public_cmrc_0090.md#ecd69a89a5d61919 |
| 硫氰酸酯指的是什么？ | 440.06 | 924.4 | 484.34 | public_cmrc_0091.md#b7b6eed3188b071a | public_cmrc_0091.md#b7b6eed3188b071a |
| 硫氰酸盐可以怎样制备？ | 412.11 | 963.82 | 551.71 | public_cmrc_0092.md#2f9d0fb5b1fb3bfb | public_cmrc_0092.md#2f9d0fb5b1fb3bfb |
| 马术比赛－团体三项赛什么时候举行？ | 385.86 | 957.05 | 571.19 | public_cmrc_0093.md#32f1ea3503c2ad9e | public_cmrc_0093.md#32f1ea3503c2ad9e |
| 决赛中哪个国家赢得了金牌？ | 549.15 | 1024.45 | 475.3 | public_cmrc_0094.md#8b66fde89291f862 | public_cmrc_0094.md#8b66fde89291f862 |
| 剩下队伍与前三名的差距如何？ | 403.71 | 948.22 | 544.51 | public_cmrc_0095.md#9f0ed6fd4edaac06 | public_cmrc_0095.md#9f0ed6fd4edaac06 |
| 岸本早未是哪个国家的歌手？ | 426.08 | 1043.29 | 617.21 | public_cmrc_0096.md#aa164a443aa56af3 | public_cmrc_0096.md#aa164a443aa56af3 |
| 岸本早未所属唱片公司是哪家？ | 409.48 | 991.67 | 582.19 | public_cmrc_0097.md#c51e3f528db04c7f | public_cmrc_0097.md#c51e3f528db04c7f |
| 2002年，岸本早未参加了什么节目与Giza签约？ | 178.2 | 877.33 | 699.13 | public_cmrc_0098.md#2405f3c96562a175 | public_cmrc_0098.md#2405f3c96562a175 |
| 最新发布的歌曲名称叫什么？ | 169.17 | 1058.1 | 888.93 | public_cmrc_0099.md#29225410dcc5b2e2 | public_cmrc_0099.md#29225410dcc5b2e2 |
| 岸本早未的服装风格受到谁的影响？ | 178.94 | 774.82 | 595.88 | public_cmrc_0100.md#ef027189d5ade2a3 | public_cmrc_0100.md#ef027189d5ade2a3 |
| 米象的俗称是什么？ | 350.63 | 786.6 | 435.97 | public_cmrc_0101.md#fcc9d7f2df92daa2 | public_cmrc_0101.md#fcc9d7f2df92daa2 |
| 除米象的正确做法是什么？ | 407.04 | 880.88 | 473.84 | public_cmrc_0102.md#3dcc359411790f22 | public_cmrc_0102.md#3dcc359411790f22 |
| 成虫是如何产卵的？ | 391.02 | 915.6 | 524.58 | public_cmrc_0103.md#03c08ad0f16c779a | public_cmrc_0103.md#03c08ad0f16c779a |
| 温度对于米象有什么影响？ | 335.09 | 884.73 | 549.64 | public_cmrc_0104.md#08b02de412ab3109 | public_cmrc_0104.md#08b02de412ab3109 |
| 米象一年约能产生多少世代？ | 559.18 | 931.98 | 372.8 | public_cmrc_0105.md#f2f8683daa17e804 | public_cmrc_0105.md#f2f8683daa17e804 |
| 大卫·克劳斯的职业是什么？ | 463.87 | 1028.79 | 564.92 | public_cmrc_0009.md#84c934d7fa5fb78a | public_cmrc_0010.md#88171c5f427ce8d8 |
| 大卫·克劳斯出生在哪儿？ | 536.12 | 841.18 | 305.06 | public_cmrc_0382.md#39a1108819816746 | public_cmrc_0009.md#84c934d7fa5fb78a |
| 大卫·克劳斯的第一部电影是什么？ | 592.35 | 917.71 | 325.36 | public_cmrc_0179.md#175b8e4fbd7e9bb8 | public_cmrc_0248.md#a8b05a4046cf8736 |
| 什么是巴士系数？ | 435.42 | 855.85 | 420.43 | public_cmrc_0109.md#9d0289cf682c7e1c | public_cmrc_0109.md#9d0289cf682c7e1c |
| 对关键成员是如何诠释的？ | 407.29 | 936.45 | 529.16 | public_cmrc_0110.md#e76ae37c354bfd70 | public_cmrc_0110.md#e76ae37c354bfd70 |
| 作为术语的“巴士系数”，是何时较为常见的？ | 605.57 | 941.63 | 336.06 | public_cmrc_0111.md#7eb9987e47e7296e | public_cmrc_0111.md#7eb9987e47e7296e |
| 巴士系数的创始人是谁？ | 493.04 | 807.07 | 314.03 | public_cmrc_0112.md#8eab97456db1d4d1 | public_cmrc_0112.md#8eab97456db1d4d1 |
| GS的热潮急速减退是什么时期？ | 562.97 | 825.5 | 262.53 | public_cmrc_0113.md#cc9a3d29f04aa179 | public_cmrc_0113.md#cc9a3d29f04aa179 |
| PYG是由哪些成员重组而成的？ | 382.42 | 787.4 | 404.98 | public_cmrc_0114.md#0ecd19217d60235e | public_cmrc_0114.md#0ecd19217d60235e |
| 在今天，一般提及的Group Sounds都指的是什么乐队？ | 558.56 | 854.92 | 296.36 | public_cmrc_0115.md#5b4b8c138f56f540 | public_cmrc_0115.md#5b4b8c138f56f540 |
| GS名字的由来是什么？ | 576.08 | 785.06 | 208.98 | public_cmrc_0116.md#2aa866502d921db0 | public_cmrc_0116.md#2aa866502d921db0 |
| 奥斯卡最佳视觉效果奖早期被称之为什么？ | 601.2 | 789.05 | 187.85 | public_cmrc_0120.md#251aefe29457c31b | public_cmrc_0120.md#251aefe29457c31b |
| 1963年，奥斯卡最佳视觉效果奖又有了什么变化？ | 608.5 | 587.54 | -20.96 | public_cmrc_0120.md#251aefe29457c31b | public_cmrc_0120.md#251aefe29457c31b |
| 该奖项是在何时更名为最佳视觉效果奖的？ | 533.45 | 918.89 | 385.44 | public_cmrc_0120.md#251aefe29457c31b | public_cmrc_0120.md#251aefe29457c31b |
| 什么是奥斯卡最佳视觉效果奖？ | 517.25 | 829.94 | 312.69 | public_cmrc_0120.md#251aefe29457c31b | public_cmrc_0120.md#251aefe29457c31b |
| 此奖项是何时创立的？ | 635.75 | 956.88 | 321.13 | public_cmrc_0324.md#203e3d52c846e591 | public_cmrc_0119.md#7874135dc33be4a0 |
| 非洲羽毛球联合会什么时候宣布退出国际羽联？ | 180.23 | 728.39 | 548.16 | public_cmrc_0122.md#75a91043dd40f27a | public_cmrc_0122.md#75a91043dd40f27a |
| 世界羽毛球联合会最终与哪一组织合并？ | 275.81 | 899.17 | 623.36 | public_cmrc_0123.md#07e7949ccffbd136 | public_cmrc_0123.md#07e7949ccffbd136 |
| 曼努乔是哪国人？ | 370.89 | 889.24 | 518.35 | public_cmrc_0124.md#2c4a2e8a7dffe685 | public_cmrc_0124.md#2c4a2e8a7dffe685 |
| 非国杯神射手冠军是谁？ | 265.39 | 819.97 | 554.58 | public_cmrc_0125.md#0091f798a75e3e5d | public_cmrc_0125.md#0091f798a75e3e5d |
| 格里戈里是哪个国家的著名导演？ | 423.01 | 900.38 | 477.37 | public_cmrc_0126.md#fdc0d66b19cbb1b5 | public_cmrc_0126.md#fdc0d66b19cbb1b5 |
| 格里戈里的常见风格是怎样的？ | 438.75 | 996.7 | 557.95 | public_cmrc_0127.md#b9be6ec4c1034585 | public_cmrc_0127.md#b9be6ec4c1034585 |
| 格里戈里曾经就读于哪所高校？ | 367.36 | 815.65 | 448.29 | public_cmrc_0128.md#5d7ba9857f2bb11f | public_cmrc_0128.md#5d7ba9857f2bb11f |
| 执导的电影中，较为出名的有哪些？ | 415.31 | 901.65 | 486.34 | public_cmrc_0129.md#30498b15aaf82f5e | public_cmrc_0129.md#30498b15aaf82f5e |
| 电影代表作是哪部？ | 549.93 | 840.17 | 290.24 | public_cmrc_0130.md#7232ad11e7f580c3 | public_cmrc_0130.md#7232ad11e7f580c3 |
| 卡卡啄羊鹦鹉的羽毛是什么颜色？ | 569.98 | 968.76 | 398.78 | public_cmrc_0131.md#491b5d223d4c2dd7 | public_cmrc_0131.md#491b5d223d4c2dd7 |
| 卡卡啄羊鹦鹉生活在什么地区？ | 495.72 | 944.36 | 448.64 | public_cmrc_0132.md#2cf3eed674dbf2ac | public_cmrc_0132.md#2cf3eed674dbf2ac |
| 卡卡啄羊鹦鹉是谁发现的？ | 554.17 | 806.99 | 252.82 | public_cmrc_0133.md#d0941a4ee3191b8b | public_cmrc_0133.md#d0941a4ee3191b8b |
| 卡卡啄羊鹦鹉的数目为什么会大大减少？ | 501.04 | 865.06 | 364.02 | public_cmrc_0134.md#ad1ce7ef22a2c03a | public_cmrc_0134.md#ad1ce7ef22a2c03a |
| 最后饲养的卡卡啄羊鹦鹉什么时候死去的？ | 529.47 | 1042.85 | 513.38 | public_cmrc_0135.md#02497618ff41c3fa | public_cmrc_0135.md#d40f6cdef6c3d8d3 |
| 《黄国伦的异想世界》开播时播出的时间段为什么时候？ | 465.38 | 1059.62 | 594.24 | public_cmrc_0136.md#0758b4da60b7c282 | public_cmrc_0136.md#0758b4da60b7c282 |
| 《黄国伦的异想世界》的主持人是谁？ | 419.34 | 1077.03 | 657.69 | public_cmrc_0137.md#ee19c500e1e83564 | public_cmrc_0137.md#ee19c500e1e83564 |
| 《福临满门感恩迎新年》的开头合唱是谁唱的？ | 363.87 | 980.63 | 616.76 | public_cmrc_0138.md#5e0e64cb411b9b1c | public_cmrc_0138.md#5e0e64cb411b9b1c |
| 海柏花园有哪些配套设施？ | 370.15 | 948.39 | 578.24 | public_cmrc_0139.md#358930d1efe81a0d | public_cmrc_0139.md#358930d1efe81a0d |
| 海柏花园在哪里？ | 511.25 | 1040.84 | 529.59 | public_cmrc_0140.md#3ea5f626c46f8f41 | public_cmrc_0140.md#3ea5f626c46f8f41 |
| 什么是笔迹分析？ | 493.98 | 1939.19 | 1445.21 | public_cmrc_0141.md#03ad4a08c56b82d4 | public_cmrc_0141.md#03ad4a08c56b82d4 |
| 通过笔迹分析，可以了解到什么内容？ | 538.21 | 1430.97 | 892.76 | public_cmrc_0142.md#505cc1edef8d3904 | public_cmrc_0142.md#505cc1edef8d3904 |
| 分析笔迹从哪几个方面入手？ | 532.33 | 1248.83 | 716.5 | public_cmrc_0143.md#f7f41439073e9126 | public_cmrc_0143.md#f7f41439073e9126 |
| 美国公司为什么注重笔迹分析？ | 585.0 | 909.19 | 324.19 | public_cmrc_0144.md#efa05600887ffd10 | public_cmrc_0144.md#efa05600887ffd10 |
| 人书写的方法可以反映什么？ | 626.73 | 1011.17 | 384.44 | public_cmrc_0145.md#24892bfefaf3c4a9 | public_cmrc_0145.md#24892bfefaf3c4a9 |
| 《暮蝉悲鸣时祭 盥回篇》是为了什么而特别设计的？ | 195.59 | 1187.47 | 991.88 | public_cmrc_0146.md#f79cffa8cf17c15c | public_cmrc_0146.md#f79cffa8cf17c15c |
| 本篇讲述了一个什么样的故事？ | 189.36 | 1096.66 | 907.3 | public_cmrc_0147.md#c69c8e14d4167d7f | public_cmrc_0147.md#c69c8e14d4167d7f |
| 为什么之后的鬼隐篇疑神疑鬼要素没有触发？ | 172.59 | 919.51 | 746.92 | public_cmrc_0148.md#cb06ba223d065f78 | public_cmrc_0148.md#cb06ba223d065f78 |
| 魅音进入“脑瘫”的状态后，对什么出现反应？ | 226.88 | 679.5 | 452.62 | public_cmrc_0149.md#9b4a383c57e579e6 | public_cmrc_0149.md#9b4a383c57e579e6 |
| 魅音恢复意识的过程是什么样的？ | 373.62 | 843.98 | 470.36 | public_cmrc_0150.md#176e31965099c599 | public_cmrc_0150.md#176e31965099c599 |
| 詹森在国民普选中获得多少支持率？ | 287.64 | 946.27 | 658.63 | public_cmrc_0151.md#073311dfca88138e | public_cmrc_0151.md#073311dfca88138e |
| 美国总统选举史上还有哪些差额很大的选举？ | 407.67 | 904.91 | 497.24 | public_cmrc_0152.md#5ec8317f8cd1ecda | public_cmrc_0152.md#5ec8317f8cd1ecda |
| 芝加哥哥伦布纪念博览会的举办时间是什么时候？ | 373.83 | 1006.45 | 632.62 | public_cmrc_0153.md#9560f8db8ff4e9f1 | public_cmrc_0153.md#9560f8db8ff4e9f1 |
| 芝加哥哥伦布纪念博览会简称为什么？ | 279.53 | 989.9 | 710.37 | public_cmrc_0154.md#a5c91f7c0c154e22 | public_cmrc_0154.md#a5c91f7c0c154e22 |
| 芝加哥哥伦布纪念博览会为了纪念谁发现新大陆400周年？ | 346.72 | 1298.15 | 951.43 | public_cmrc_0155.md#fd4aaeae02ee5715 | public_cmrc_0155.md#fd4aaeae02ee5715 |
| 主会场Court of Honor地区有哪些展馆？ | 276.01 | 1110.31 | 834.3 | public_cmrc_0156.md#df26fe7e89a52450 | public_cmrc_0156.md#df26fe7e89a52450 |
| 孙晋芳现任什么职务？ | 312.18 | 933.04 | 620.86 | public_cmrc_0157.md#4ca69ae692ad1483 | public_cmrc_0157.md#4ca69ae692ad1483 |
| 中国女排的首个世界冠军是什么？ | 430.64 | 991.01 | 560.37 | public_cmrc_0158.md#199d7c0ea8f547b6 | public_cmrc_0158.md#199d7c0ea8f547b6 |
| 在担任国家体育彩票中心主任期间，有哪些杰出成就？ | 454.61 | 2196.15 | 1741.54 | public_cmrc_0159.md#aed86e9a881e1283 | public_cmrc_0159.md#aed86e9a881e1283 |
| 在担任国家体育总局网球运动管理中心主任时，有什么特殊贡献？ | 403.55 | 1596.51 | 1192.96 | public_cmrc_0160.md#14f03b09c5dbcd2e | public_cmrc_0160.md#14f03b09c5dbcd2e |
| 什么是天正之阵？ | 393.27 | 1160.32 | 767.05 | public_cmrc_0161.md#5429c640aada09eb | public_cmrc_0161.md#5429c640aada09eb |
| 秀吉四国征伐中最重要的一战是什么？ | 543.1 | 1073.44 | 530.34 | public_cmrc_0162.md#2f5f81d6d2e70787 | public_cmrc_0162.md#2f5f81d6d2e70787 |
| 7月17日，金子元宅放火烧掉高尾城后做了什么？ | 400.08 | 1043.53 | 643.45 | public_cmrc_0163.md#c8e021bf614748ee | public_cmrc_0163.md#c8e021bf614748ee |
| 长宗我部元亲为什么向秀吉投降？ | 515.33 | 938.18 | 422.85 | public_cmrc_0164.md#0d340129dcf2f95e | public_cmrc_0164.md#0d340129dcf2f95e |
| 什么是化学物质毒性数据库？ | 685.49 | 1119.41 | 433.92 | public_cmrc_0165.md#590740aeaa19ace8 | public_cmrc_0165.md#590740aeaa19ace8 |
| 在2001年之前这个数据库是哪个公司提供的出版物？ | 639.48 | 1030.48 | 391.0 | public_cmrc_0166.md#22926d6acc25cc73 | public_cmrc_0166.md#22926d6acc25cc73 |
| RTECS中主要包括几类化学物质的毒性数据？ | 590.02 | 930.05 | 340.03 | public_cmrc_0167.md#457e53019e25a5ac | public_cmrc_0167.md#457e53019e25a5ac |
| RTECS有几个语言版本？ | 669.14 | 814.85 | 145.71 | public_cmrc_0168.md#fa138c6da03a0cc3 | public_cmrc_0168.md#fa138c6da03a0cc3 |
| 江背镇位于什么地方？ | 571.05 | 965.49 | 394.44 | public_cmrc_0169.md#892af22ed99215e6 | public_cmrc_0169.md#892af22ed99215e6 |
| 什么时候三乡镇合并组建江背镇？ | 380.54 | 1075.69 | 695.15 | public_cmrc_0170.md#f538c1c29a0b77c4 | public_cmrc_0170.md#f538c1c29a0b77c4 |
| 区划调整后村落和社区有什么变化？ | 362.31 | 1271.55 | 909.24 | public_cmrc_0171.md#1f314b3ca06f66f3 | public_cmrc_0171.md#1f314b3ca06f66f3 |
| 哪一年乙二胺的使用量约500,000,000公斤？ | 305.12 | 930.96 | 625.84 | public_cmrc_0172.md#2b4d7f534d44279b | public_cmrc_0172.md#2b4d7f534d44279b |
| 乙二胺有类似氨的什么气味？ | 322.2 | 1311.66 | 989.46 | public_cmrc_0173.md#36924680eb61801b | public_cmrc_0173.md#36924680eb61801b |
| 乙二胺有二个什么？ | 195.62 | 1118.89 | 923.27 | public_cmrc_0174.md#e4a32d558d05e686 | public_cmrc_0174.md#e4a32d558d05e686 |
| 乙二胺为什么是许多聚合物制造的原料之一？ | 304.75 | 990.78 | 686.03 | public_cmrc_0175.md#e15cb127952a96fb | public_cmrc_0175.md#e15cb127952a96fb |
| 钱小豪在哪儿长大？ | 248.66 | 768.35 | 519.69 | public_cmrc_0176.md#69a3c9dee78df388 | public_cmrc_0176.md#69a3c9dee78df388 |
| 钱小豪原名叫什么？ | 312.85 | 855.22 | 542.37 | public_cmrc_0177.md#5c08a369bb80ad7f | public_cmrc_0177.md#5c08a369bb80ad7f |
| 钱小豪出生在哪儿？ | 688.64 | 844.07 | 155.43 | public_cmrc_0178.md#bffeeec4765fe74e | public_cmrc_0178.md#bffeeec4765fe74e |
| 钱小豪第一部担任男主角的影片是什么？ | 1023.26 | 1170.08 | 146.82 | public_cmrc_0179.md#175b8e4fbd7e9bb8 | public_cmrc_0176.md#06dfe034b626c91d |
| 五羊邨站的公共洗手间位于哪儿？ | 986.44 | 1265.8 | 279.36 | public_cmrc_0180.md#ef46c97d4d474403 | public_cmrc_0180.md#ef46c97d4d474403 |
| 五羊邨站位于哪里？ | 964.2 | 1292.65 | 328.45 | public_cmrc_0181.md#f62fb1e2f352a03c | public_cmrc_0181.md#f62fb1e2f352a03c |
| 五羊邨站共有几层？ | 1004.72 | 1236.76 | 232.04 | public_cmrc_0182.md#c01020a835f1028e | public_cmrc_0182.md#c01020a835f1028e |
| 付费区站厅与站台之间有哪些便利设施？ | 858.96 | 1184.23 | 325.27 | public_cmrc_0183.md#47b57297cd9c1f9d | public_cmrc_0183.md#47b57297cd9c1f9d |
| 周而复什么时候出版了第一本诗集？ | 682.63 | 1056.75 | 374.12 | public_cmrc_0184.md#499eceee0b78badd | public_cmrc_0184.md#499eceee0b78badd |
| 周而复为什么会被开除党籍？ | 659.14 | 1158.52 | 499.38 | public_cmrc_0185.md#df24414afc2e97a7 | public_cmrc_0185.md#df24414afc2e97a7 |
| 奥亚吸蜜鸟是哪里的特有物种？ | 731.2 | 1195.16 | 463.96 | public_cmrc_0186.md#25a7b3b1b2641d38 | public_cmrc_0186.md#25a7b3b1b2641d38 |
| 奥亚吸蜜鸟灭绝的原因是什么？ | 791.92 | 1444.46 | 652.54 | public_cmrc_0187.md#9932c0f2842a8ec4 | public_cmrc_0187.md#9932c0f2842a8ec4 |
| 奥亚吸蜜鸟体积有多大？ | 958.76 | 854.32 | -104.44 | public_cmrc_0188.md#cee385331a211b28 | public_cmrc_0188.md#cee385331a211b28 |
| 奥亚吸蜜鸟以什么为食？ | 770.61 | 905.56 | 134.95 | public_cmrc_0189.md#2d1890a84d3804ac | public_cmrc_0189.md#2d1890a84d3804ac |
| 奥亚吸蜜鸟的近亲有什么？ | 311.97 | 682.54 | 370.57 | public_cmrc_0190.md#e4c4d62193c815d4 | public_cmrc_0190.md#e4c4d62193c815d4 |
| 《咒法解禁!! HYDE & CROSER》是谁的作品？ | 730.38 | 804.32 | 73.94 | public_cmrc_0236.md#fab016c03c2cce01 | public_cmrc_0234.md#a9027dc8577189bc |
| 主角实际上继承了谁的血脉？ | 1143.47 | 1223.3 | 79.83 | public_cmrc_0234.md#a9027dc8577189bc | public_cmrc_0234.md#a9027dc8577189bc |
| 咒术的媒介「咒具」在哪里可以购买？ | 1478.68 | 907.67 | -571.01 | public_cmrc_0235.md#2dfe034595951c90 | public_cmrc_0235.md#2dfe034595951c90 |
| 罗宾在吉布斯家族五姐弟排行第几？ | 1353.97 | 866.89 | -487.08 | public_cmrc_0194.md#9b94b5e6f87b546c | public_cmrc_0194.md#9b94b5e6f87b546c |
| 罗宾的双胞胎弟弟叫什么？ | 1340.35 | 961.86 | -378.49 | public_cmrc_0195.md#403cb452c439d97b | public_cmrc_0195.md#403cb452c439d97b |

## Quality Summary (Gold-labeled)

| scenario | citation_hit@1 | citation_hit@k | MRR | nDCG@3 | nDCG@5 | nDCG@k | answer_substring_match | answer_char_f1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 0.94 | 0.94 | 0.94 | 1.16 | 1.23 | 1.23 | 1.0 | 0.0 |
| rerank_on | 0.94 | 0.94 | 0.94 | 1.06 | 1.06 | 1.06 | 1.0 | 0.0 |

## Top nDCG Degradation Cases

| query | base_nDCG@5 | rerank_nDCG@5 | delta | base_top_citation | rerank_top_citation |
|---|---:|---:|---:|---|---|
| 周而复什么时候出版了第一本诗集？ | 2.061606 | 1.0 | -1.061606 | public_cmrc_0184.md#499eceee0b78badd | public_cmrc_0184.md#499eceee0b78badd |
| 钱小豪第一部担任男主角的影片是什么？ | 1.5 | 0.63093 | -0.86907 | public_cmrc_0179.md#175b8e4fbd7e9bb8 | public_cmrc_0176.md#06dfe034b626c91d |
| 王栋为什么戴着保护面具参加了深圳红钻的数场保级战役？ | 1.63093 | 1.0 | -0.63093 | public_cmrc_0042.md#3f2cb867363d03f3 | public_cmrc_0042.md#3f2cb867363d03f3 |
| 华阳路街道四周相连的是什么地方？ | 1.63093 | 1.0 | -0.63093 | public_cmrc_0052.md#e0f80dc93d253924 | public_cmrc_0052.md#e0f80dc93d253924 |
| 华阳路街道下辖多少个居委会？ | 1.63093 | 1.0 | -0.63093 | public_cmrc_0053.md#d3c17a9e938db785 | public_cmrc_0053.md#d3c17a9e938db785 |
| 对崇庆县开始实施军事管制是哪一个事件？ | 1.63093 | 1.0 | -0.63093 | public_cmrc_0085.md#e36ba8f6c194faaa | public_cmrc_0085.md#e36ba8f6c194faaa |
| 电影中的旁白由谁担纲负责？ | 1.63093 | 1.0 | -0.63093 | public_cmrc_0087.md#02c8508a3ad7e327 | public_cmrc_0087.md#02c8508a3ad7e327 |
| 硫氰酸盐可以怎样制备？ | 1.63093 | 1.0 | -0.63093 | public_cmrc_0092.md#2f9d0fb5b1fb3bfb | public_cmrc_0092.md#2f9d0fb5b1fb3bfb |
| 马术比赛－团体三项赛什么时候举行？ | 1.63093 | 1.0 | -0.63093 | public_cmrc_0093.md#32f1ea3503c2ad9e | public_cmrc_0093.md#32f1ea3503c2ad9e |
| 卡卡啄羊鹦鹉生活在什么地区？ | 1.63093 | 1.0 | -0.63093 | public_cmrc_0132.md#2cf3eed674dbf2ac | public_cmrc_0132.md#2cf3eed674dbf2ac |

## Conclusion

- rerank结论：`NOT PASS`。nDCG@5 `-0.17`，MRR `0.0`，citation_hit@1 `0.0`，p95 RT 变化 `616.36` ms。
- 失败原因：nDCG@5未达阈值, MRR未达阈值, p95 RT超阈值

## Improvement Suggestions

- 优先调大候选池：`RAG_RERANK_CANDIDATES` 从 10 提升到 20/30 再复测。
- 抽查 `Top nDCG Degradation Cases`，确认是否金标粒度与chunk粒度不一致。
- 若尾延迟上升明显，建议降低 rerank 候选数或仅对高风险问题启用rerank。
