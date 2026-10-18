这是项目生成的数值训练缓存，不是原始回放、游戏资源或官方轨迹真值。
只含白名单actor特征x、监督target/xy及当时记录的mask；没有姓名、原始payload、来源对局ID、原生隐藏状态或完整动作日志。train/validation/test保持原整局分组。
复现时重用已有质量筛选结果，不重新证明原生合法性或官方一致；原始证明的部分外部依赖另备。只有52次出牌，不能将18,440行当作强策略训练经验。
TRAIN_FROM_CACHE.py使用实际项目的损失和梯度，并按原checkpoint配置重跑优化。
数值缓存与权重均为非object数组，加载allow_pickle=False。
{
  "train": {
    "decisions": 13431,
    "episodes": 9,
    "play_labels": 31
  },
  "validation": {
    "decisions": 3348,
    "episodes": 2,
    "play_labels": 12
  },
  "test": {
    "decisions": 1661,
    "episodes": 1,
    "play_labels": 9
  }
}