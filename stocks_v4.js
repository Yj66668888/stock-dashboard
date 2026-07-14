const STOCKS = [
  // ===== davis 策略 (25只) =====
  // 招商证券 - 券商，无双轮驱动
  {code:"sh600999",name:"招商证券",industry:"券商",tier:"tier3",theme:"券商龙头+低估值",price:22.94,changePct:8.72,pe:"14.99",pe26e:"13",growth26e:"10%",roe:"6%",stars:2,catalyst:"资本市场回暖",dual:"none",supply:"",demand:""},
  // 艾迪精密 - 工程机械零部件，供给端（行业整合）
  {code:"sh603638",name:"艾迪精密",industry:"工程机械",tier:"tier2",theme:"液压件+低估值",price:23.52,changePct:0.3,pe:"45.97",pe26e:"33",growth26e:"20%",roe:"12%",stars:3,catalyst:"工程机械复苏",dual:"supply",supply:"行业整合",demand:""},
  // 洛阳钼业 - 小金属，供给端（资源整合）
  {code:"sh603993",name:"洛阳钼业",industry:"小金属",tier:"tier2",theme:"铜钴双轮+新能源金属",price:17.73,changePct:1.03,pe:"15.71",pe26e:"12",growth26e:"22%",roe:"29%",stars:3,catalyst:"铜价高位",dual:"supply",supply:"资源整合",demand:"新能源需求"},
  // 宏桥控股 - 铝业，供给端（产能控制）
  {code:"sz002379",name:"宏桥控股",industry:"有色金属",tier:"tier2",theme:"铝业龙头+低估值",price:14.58,changePct:-1.29,pe:"9.64",pe26e:"7",growth26e:"15%",roe:"40%",stars:4,catalyst:"铝价回升",dual:"supply",supply:"产能控制",demand:""},
  // 圆通速递 - 快递，需求端（电商）
  {code:"sh600233",name:"圆通速递",industry:"快递物流",tier:"tier2",theme:"快递龙头+低估值",price:17.33,changePct:10.03,pe:"12.25",pe26e:"10",growth26e:"18%",roe:"14%",stars:4,catalyst:"电商复苏",dual:"demand",supply:"",demand:"电商快递增长"},
  // 中国重汽 - 重卡，需求端（换车周期）
  {code:"sz000951",name:"中国重汽",industry:"商用车",tier:"tier2",theme:"重卡龙头+低估值",price:19.85,changePct:1.74,pe:"12.82",pe26e:"12",growth26e:"15%",roe:"11%",stars:3,catalyst:"换车周期",dual:"demand",supply:"",demand:"换车周期+基建"},
  // 亚星锚链 - 船舶零部件，无
  {code:"sh601890",name:"亚星锚链",industry:"船舶零部件",tier:"tier3",theme:"锚链龙头",price:8.3,changePct:1.59,pe:"22.8",pe26e:"20",growth26e:"10%",roe:"9%",stars:2,catalyst:"船舶周期",dual:"none",supply:"",demand:""},
  // 申通快递 - 快递，需求端（电商）
  {code:"sz002468",name:"申通快递",industry:"快递物流",tier:"tier2",theme:"快递复苏+低估值",price:16.02,changePct:10.03,pe:"15.41",pe26e:"13",growth26e:"20%",roe:"14%",stars:4,catalyst:"电商复苏",dual:"demand",supply:"",demand:"电商快递增长"},
  // 小商品城 - 商贸，无
  {code:"sh600415",name:"小商品城",industry:"商贸",tier:"tier3",theme:"小商品市场",price:10.39,changePct:2.26,pe:"12.98",pe26e:"14",growth26e:"5%",roe:"21%",stars:2,catalyst:"一带一路",dual:"none",supply:"",demand:""},
  // 东鹏饮料 - 饮料，无
  {code:"sh605499",name:"东鹏饮料",industry:"食品饮料",tier:"tier2",theme:"能量饮料+高成长",price:128.53,changePct:4.13,pe:"20.11",pe26e:"18",growth26e:"18%",roe:"25%",stars:3,catalyst:"全国化",dual:"none",supply:"",demand:""},
  // 东方电子 - 电网设备，无
  {code:"sz000682",name:"东方电子",industry:"电网设备",tier:"tier3",theme:"电网设备",price:12.79,changePct:1.27,pe:"16.7",pe26e:"18",growth26e:"8%",roe:"17%",stars:2,catalyst:"电网投资",dual:"none",supply:"",demand:""},
  // 咸亨国际 - 电网设备，无
  {code:"sh605056",name:"咸亨国际",industry:"电网设备",tier:"tier3",theme:"电网设备",price:15.96,changePct:-0.25,pe:"24.75",pe26e:"62",growth26e:"5%",roe:"15%",stars:1,catalyst:"电网投资",dual:"none",supply:"",demand:""},
  // 迪普科技 - 网络安全，无
  {code:"sz300768",name:"迪普科技",industry:"网络安全",tier:"tier3",theme:"网络安全",price:14.15,changePct:2.76,pe:"46.33",pe26e:"49",growth26e:"5%",roe:"6%",stars:1,catalyst:"网络安全",dual:"none",supply:"",demand:""},
  // 金诚信 - 矿山服务，供给端（矿山整合）
  {code:"sh603979",name:"金诚信",industry:"矿山服务",tier:"tier2",theme:"矿山服务+海外",price:58.64,changePct:-0.66,pe:"14.53",pe26e:"15",growth26e:"15%",roe:"22%",stars:3,catalyst:"海外矿山",dual:"supply",supply:"矿山整合",demand:""},
  // 德业股份 - 光伏逆变器，无
  {code:"sh605117",name:"德业股份",industry:"光伏设备",tier:"tier2",theme:"光伏逆变器+储能",price:101.5,changePct:-4.41,pe:"35.37",pe26e:"27",growth26e:"30%",roe:"37%",stars:3,catalyst:"储能爆发",dual:"none",supply:"",demand:""},
  // 滨化股份 - 化工，供给端（产能整合）
  {code:"sh601678",name:"滨化股份",industry:"化工",tier:"tier2",theme:"氯碱化工+周期",price:7.0,changePct:6.22,pe:"52.22",pe26e:"24",growth26e:"50%",roe:"2%",stars:2,catalyst:"周期反转",dual:"supply",supply:"产能整合",demand:""},
  // 大金重工 - 风电设备，需求端（海风）
  {code:"sz002487",name:"大金重工",industry:"风电设备",tier:"tier2",theme:"风电塔筒+出海",price:55.04,changePct:-5.05,pe:"31.07",pe26e:"23",growth26e:"40%",roe:"15%",stars:3,catalyst:"海风放量",dual:"demand",supply:"",demand:"海风需求"},
  // 大豪科技 - 纺织设备，无
  {code:"sh603025",name:"大豪科技",industry:"纺织设备",tier:"tier3",theme:"刺绣机龙头",price:13.81,changePct:1.25,pe:"19.78",pe26e:"16",growth26e:"12%",roe:"32%",stars:2,catalyst:"纺织升级",dual:"none",supply:"",demand:""},
  // 罗莱生活 - 家纺，需求端（消费复苏）
  {code:"sz002293",name:"罗莱生活",industry:"家纺",tier:"tier2",theme:"家纺龙头+高股息",price:10.66,changePct:7.79,pe:"16.1",pe26e:"15",growth26e:"10%",roe:"14%",stars:3,catalyst:"消费复苏",dual:"demand",supply:"",demand:"消费复苏"},
  // 涪陵电力 - 电力，无
  {code:"sh600452",name:"涪陵电力",industry:"电力",tier:"tier3",theme:"配电网节能",price:9.38,changePct:0.54,pe:"33.38",pe26e:"28",growth26e:"8%",roe:"8%",stars:1,catalyst:"电网节能",dual:"none",supply:"",demand:""},
  // 陕西能源 - 电力，无
  {code:"sz001286",name:"陕西能源",industry:"电力",tier:"tier3",theme:"煤电一体化",price:10.49,changePct:3.45,pe:"12.31",pe26e:"11",growth26e:"8%",roe:"12%",stars:2,catalyst:"煤电一体化",dual:"none",supply:"",demand:""},
  // 三全食品 - 食品，需求端（消费复苏）
  {code:"sz002216",name:"三全食品",industry:"食品",tier:"tier2",theme:"速冻食品+低估值",price:12.21,changePct:5.9,pe:"17.73",pe26e:"9.97",growth26e:"20%",roe:"14%",stars:3,catalyst:"消费复苏",dual:"demand",supply:"",demand:"消费复苏"},
  // 华测检测 - 检测服务，无
  {code:"sz300012",name:"华测检测",industry:"检测服务",tier:"tier2",theme:"检测龙头+并购",price:15.04,changePct:8.51,pe:"23.91",pe26e:"35",growth26e:"15%",roe:"14%",stars:3,catalyst:"并购扩张",dual:"none",supply:"",demand:""},
  // 芭田股份 - 化肥，供给端（产能整合）
  {code:"sz002170",name:"芭田股份",industry:"化肥",tier:"tier2",theme:"磷化工+低估值",price:11.07,changePct:1.93,pe:"10.59",pe26e:"9.82",growth26e:"12%",roe:"29%",stars:4,catalyst:"磷矿资源",dual:"supply",supply:"磷矿整合",demand:""},
  // 华人健康 - 医药零售，无
  {code:"sz301408",name:"华人健康",industry:"医药零售",tier:"tier3",theme:"医药零售",price:15.16,changePct:5.42,pe:"28.95",pe26e:"19",growth26e:"25%",roe:"10%",stars:2,catalyst:"门店扩张",dual:"none",supply:"",demand:""},

  // ===== profit_preannounce 策略 (25只，去重后23只新的) =====
  // 招商轮船 - 航运，无
  {code:"sh601872",name:"招商轮船",industry:"航运",tier:"tier2",theme:"油运龙头+周期",price:16.87,changePct:-3.98,pe:"17.22",pe26e:"12",growth26e:"30%",roe:"18%",stars:3,catalyst:"油运周期",dual:"none",supply:"",demand:""},
  // 涛涛车业 - 摩托车，无
  {code:"sz301345",name:"涛涛车业",industry:"摩托车",tier:"tier2",theme:"全地形车+出海",price:227.99,changePct:-1.3,pe:"27.43",pe26e:"35",growth26e:"20%",roe:"25%",stars:3,catalyst:"出海",dual:"none",supply:"",demand:""},
  // 永杰新材 - 新材料，无
  {code:"sh603271",name:"永杰新材",industry:"新材料",tier:"tier3",theme:"新材料",price:44.86,changePct:0.92,pe:"19.37",pe26e:"18",growth26e:"10%",roe:"15%",stars:2,catalyst:"新材料应用",dual:"none",supply:"",demand:""},
  // 海通发展 - 航运，无
  {code:"sh603162",name:"海通发展",industry:"航运",tier:"tier3",theme:"干散货运输",price:9.24,changePct:3.01,pe:"20.91",pe26e:"15",growth26e:"15%",roe:"13%",stars:2,catalyst:"航运周期",dual:"none",supply:"",demand:""},
  // 瑞达期货 - 期货，无
  {code:"sz002961",name:"瑞达期货",industry:"期货",tier:"tier3",theme:"期货经纪",price:20.55,changePct:4.95,pe:"14.64",pe26e:"12",growth26e:"12%",roe:"17%",stars:2,catalyst:"资本市场回暖",dual:"none",supply:"",demand:""},
  // 恒通股份 - 物流，无
  {code:"sh603223",name:"恒通股份",industry:"物流",tier:"tier3",theme:"物流",price:13.36,changePct:2.38,pe:"29.76",pe26e:"25",growth26e:"8%",roe:"8%",stars:1,catalyst:"物流复苏",dual:"none",supply:"",demand:""},
  // 宁波韵升 - 稀土永磁，供给端（稀土整合）
  {code:"sh600366",name:"宁波韵升",industry:"稀土永磁",tier:"tier2",theme:"稀土永磁+低估值",price:15.99,changePct:8.92,pe:"42.85",pe26e:"37",growth26e:"25%",roe:"7%",stars:2,catalyst:"稀土涨价",dual:"supply",supply:"稀土整合",demand:"新能源需求"},
  // 藏格矿业 - 钾肥锂矿，供给端（资源整合）
  {code:"sz000408",name:"藏格矿业",industry:"钾肥锂矿",tier:"tier2",theme:"钾肥+锂矿",price:68.32,changePct:-1.44,pe:"22.91",pe26e:"17",growth26e:"30%",roe:"27%",stars:3,catalyst:"锂价回升",dual:"supply",supply:"资源整合",demand:""},
  // 金徽股份 - 铅锌矿，供给端（资源整合）
  {code:"sh603132",name:"金徽股份",industry:"铅锌矿",tier:"tier3",theme:"铅锌矿",price:16.07,changePct:3.15,pe:"24.05",pe26e:"19",growth26e:"12%",roe:"20%",stars:2,catalyst:"资源整合",dual:"supply",supply:"资源整合",demand:""},
  // 工业富联 - 电子制造，无
  {code:"sh601138",name:"工业富联",industry:"电子制造",tier:"tier2",theme:"AI服务器+低估值",price:70.0,changePct:-2.98,pe:"34.17",pe26e:"32",growth26e:"15%",roe:"23%",stars:2,catalyst:"AI服务器",dual:"none",supply:"",demand:""},
  // 永兴材料 - 锂矿，供给端（锂矿整合）
  {code:"sz002756",name:"永兴材料",industry:"锂矿",tier:"tier2",theme:"锂矿+不锈钢",price:58.89,changePct:3.97,pe:"33.1",pe26e:"16",growth26e:"50%",roe:"7%",stars:3,catalyst:"锂价回升",dual:"supply",supply:"锂矿整合",demand:"新能源需求"},
  // 兴业银锡 - 银矿，供给端（资源整合）
  {code:"sz000426",name:"兴业银锡",industry:"银矿",tier:"tier2",theme:"银矿龙头",price:33.04,changePct:0.24,pe:"21.99",pe26e:"10.96",growth26e:"50%",roe:"25%",stars:3,catalyst:"银价上涨",dual:"supply",supply:"资源整合",demand:""},
  // 厦门钨业 - 钨业，供给端（钨矿整合）
  {code:"sh600549",name:"厦门钨业",industry:"钨业",tier:"tier2",theme:"钨业龙头+稀土",price:84.9,changePct:-0.35,pe:"44.43",pe26e:"30",growth26e:"30%",roe:"17%",stars:3,catalyst:"钨价上涨",dual:"supply",supply:"钨矿整合",demand:"硬质合金需求"},
  // 锡业股份 - 锡业，供给端（锡矿整合）
  {code:"sz000960",name:"锡业股份",industry:"锡业",tier:"tier2",theme:"锡业龙头",price:42.32,changePct:-0.87,pe:"29.83",pe26e:"20",growth26e:"25%",roe:"11%",stars:3,catalyst:"锡价上涨",dual:"supply",supply:"锡矿整合",demand:"半导体需求"},
  // 大连电瓷 - 电网设备，无
  {code:"sz002606",name:"大连电瓷",industry:"电网设备",tier:"tier3",theme:"绝缘子",price:13.04,changePct:-2.76,pe:"22.8",pe26e:"24",growth26e:"5%",roe:"13%",stars:1,catalyst:"电网投资",dual:"none",supply:"",demand:""},
  // 中国船舶 - 船舶，无
  {code:"sh600150",name:"中国船舶",industry:"船舶",tier:"tier2",theme:"造船龙头+周期",price:35.19,changePct:4.14,pe:"23.42",pe26e:"13.7",growth26e:"40%",roe:"8%",stars:3,catalyst:"造船周期",dual:"none",supply:"",demand:""},
  // 一汽解放 - 商用车，需求端（换车周期）
  {code:"sz000800",name:"一汽解放",industry:"商用车",tier:"tier3",theme:"重卡+低估值",price:6.0,changePct:0.84,pe:"37.15",pe26e:"74",growth26e:"8%",roe:"3%",stars:1,catalyst:"换车周期",dual:"demand",supply:"",demand:"换车周期"},
  // 南华期货 - 期货，无
  {code:"sh603093",name:"南华期货",industry:"期货",tier:"tier3",theme:"期货经纪",price:19.3,changePct:6.34,pe:"22.89",pe26e:"16.91",growth26e:"15%",roe:"11%",stars:2,catalyst:"资本市场回暖",dual:"none",supply:"",demand:""},
  // 中金黄金 - 黄金，无
  {code:"sh600489",name:"中金黄金",industry:"黄金",tier:"tier2",theme:"黄金龙头+低估值",price:17.96,changePct:-0.5,pe:"13.87",pe26e:"9.14",growth26e:"20%",roe:"19%",stars:4,catalyst:"金价高位",dual:"none",supply:"",demand:""},
  // 若羽臣 - 电商服务，无
  {code:"sz003010",name:"若羽臣",industry:"电商服务",tier:"tier3",theme:"电商服务",price:24.63,changePct:1.36,pe:"32.01",pe26e:"26.48",growth26e:"15%",roe:"34%",stars:2,catalyst:"电商复苏",dual:"none",supply:"",demand:""},
  // 赤峰黄金 - 黄金，无
  {code:"sh600988",name:"赤峰黄金",industry:"黄金",tier:"tier2",theme:"黄金+低估值",price:25.81,changePct:-1.94,pe:"13.67",pe26e:"12.41",growth26e:"18%",roe:"26%",stars:4,catalyst:"金价高位",dual:"none",supply:"",demand:""},
  // 泛微网络 - 软件，无
  {code:"sh603039",name:"泛微网络",industry:"软件",tier:"tier3",theme:"OA软件",price:36.13,changePct:0.17,pe:"34.23",pe26e:"44.72",growth26e:"5%",roe:"12%",stars:1,catalyst:"数字化转型",dual:"none",supply:"",demand:""}
];
