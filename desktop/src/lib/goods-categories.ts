import type { GoodsRecord } from "@/lib/types"

export const GOODS_CATEGORY_TREE: Record<string, string[]> = {
  装备: [
    "头盔",
    "面罩",
    "防弹衣",
    "无甲单挂",
    "有甲弹挂",
    "背包",
    "耳机 -防毒面具",
  ],
  武器配件: [
    "瞄具",
    "弹匣",
    "前握把",
    "后握把",
    "枪托",
    "枪口",
    "镭指器",
    "枪管",
    "护木",
    "机匣&防尘盖",
    "导轨",
    "导气箍",
    "枪栓",
    "手电",
  ],
  武器枪机: [
    "突击步枪",
    "冲锋枪",
    "霰弹枪",
    "轻机枪",
    "栓动步枪",
    "射手步枪",
    "卡宾枪",
    "手枪",
  ],
  弹药: [
    "5.45×39毫米子弹",
    "5.56×45毫米子弹",
    "5.7×28毫米子弹",
    "5.8×42毫米子弹",
    "7.62×25毫米子弹",
    "7.62×39毫米子弹",
    "7.62×51毫米子弹",
    "7.62×54毫米子弹",
    "9×19毫米子弹",
    "9×39毫米子弹",
    "12×70毫米子弹",
    ".44口径子弹",
    ".45口径子弹",
    ".338口径子弹",
  ],
  医疗用品: ["药物", "伤害救治", "医疗包", "药剂"],
  战术道具: ["投掷物"],
  钥匙: ["农场钥匙", "北山钥匙", "山谷钥匙", "前线要塞钥匙", "电视台钥匙"],
  杂物: [
    "易燃物品",
    "建筑材料",
    "电脑配件",
    "能源物品",
    "工具",
    "生活用品",
    "医疗杂物",
    "收藏品",
    "纸制品",
    "仪器仪表",
    "军用杂物",
    "首领信物",
    "电子产品",
  ],
  饮食: ["饮料", "食品"],
}

export type GoodsCategoryNode = {
  name: string
  count: number
  subcategories: Array<{
    name: string
    count: number
  }>
}

export function buildGoodsCategoryTree(goods: GoodsRecord[]): GoodsCategoryNode[] {
  const bigMap = new Map<string, Map<string, number>>()

  for (const [bigCategory, subcategories] of Object.entries(GOODS_CATEGORY_TREE)) {
    const subMap = new Map<string, number>()
    for (const sub of subcategories) {
      subMap.set(sub, 0)
    }
    bigMap.set(bigCategory, subMap)
  }

  for (const item of goods) {
    const big = item.bigCategory?.trim() || "未分类"
    const sub = item.subCategory?.trim() || "未细分"
    if (!bigMap.has(big)) {
      bigMap.set(big, new Map())
    }
    const subMap = bigMap.get(big)!
    subMap.set(sub, (subMap.get(sub) ?? 0) + 1)
  }

  return Array.from(bigMap.entries())
    .map(([name, subMap]) => ({
      name,
      count: Array.from(subMap.values()).reduce((sum, value) => sum + value, 0),
      subcategories: Array.from(subMap.entries())
        .map(([subName, count]) => ({ name: subName, count }))
        .sort((a, b) => a.name.localeCompare(b.name, "zh-CN")),
    }))
    .sort((a, b) => a.name.localeCompare(b.name, "zh-CN"))
}

export function defaultSubcategories(bigCategory: string) {
  return GOODS_CATEGORY_TREE[bigCategory] ?? []
}
