/** Public-domain AIC images; project-authored Chinese examples, not official scripts. */
export type DemoStyle = "brief" | "children" | "deep";
export const demoWorks = [
  {id:"artic-16568", title:"Water Lilies", name:"睡莲", artist:"克洛德·莫奈", year:"1906", number:"1933.1157", keys:["睡莲","water lilies","莫奈","monet","池塘"],
    brief:"这幅《睡莲》由莫奈创作于1906年。视线集中在水面：睡莲、树木与云的倒影交织在一起。先找找左下和右上两组睡莲，再看看它们之间留出的水面。",
    children:"这是莫奈画的《睡莲》。水面像一面会晃动的镜子，映着树和云。你能找到睡莲聚在一起的两个位置吗？试着分一分，哪些是花，哪些是倒影。",
    deep:"这件1906年的《睡莲》把观看范围收在水面，弱化了通常风景画中陆地、天空与水的分区。左下和右上的睡莲围出中间的水面通道，树与云则以倒影出现。它属于莫奈在1903至1908年间创作的一组同题材作品；这里讲的是芝加哥艺术博物馆收藏的这一件，不代表所有《睡莲》都具有相同年代与构图。"},
  {id:"artic-28560", title:"The Bedroom", name:"卧室", artist:"文森特·梵高", year:"1889", number:"1926.417", keys:["卧室","bedroom","梵高","van gogh","床"],
    brief:"这是梵高1889年画的《卧室》，描绘他在法国阿尔勒黄房子里的卧室。鲜明的色彩和向内延伸的线条很醒目，但画家希望这幅画传达休息与安宁。",
    children:"看看梵高画的房间：床、椅子和桌子都用了很鲜明的颜色。他希望这幅画让人想到休息。你会先看到哪件家具？再找找画中有几把椅子。",
    deep:"梵高一共画过三个版本的这一卧室场景。芝加哥艺术博物馆收藏的是第二版，完成于1889年9月，距第一版约一年。强烈的颜色、厚重而断续的笔触和向深处收拢的线条可能让人觉得有些紧张，而画家本人将它理解为安静、适于休息的图像。"},
  {id:"artic-27992", title:"A Sunday on La Grande Jatte — 1884", name:"大碗岛的星期天下午", artist:"乔治·修拉", year:"1884–1886；边框于1888–1889年添加", number:"1926.224", keys:["大碗岛","星期天下午","修拉","seurat","grande jatte","点彩"],
    brief:"修拉用这幅画描绘塞纳河畔的人群。离近看，人物与景物由许多并置的小色点和短笔触组成；退远看，它们又汇成完整的场景。这是观察点彩技法的好例子。",
    children:"修拉把许多小色点排在一起，画出了河边散步和休息的人。试试靠近图片看颜色，再退远看人物。你还能找到画里的小猴子吗？",
    deep:"这幅作品主要创作于1884至1886年，边框后来于1888至1889年添加。修拉通过并置色点与短笔触组织画面，并在创作过程中调整人物的轮廓、加入其他人物。近看分散的笔触与远看整体的场景形成了两种观看体验；题名中的1884不能代替整件作品的完整创作时间。"},
] as const;
export type DemoWork = typeof demoWorks[number];
export const sourceFor = (work: DemoWork) => `https://www.artic.edu/artworks/${work.id.replace("artic-", "")}`;
export function findDemoWorks(query: string) {
  const q=query.normalize("NFKC").toLowerCase();
  return demoWorks.filter(work=>q.includes(work.number) || work.keys.some(key=>q.includes(key)));
}
export function demoAnswer(work: DemoWork, query: string, style: DemoStyle) {
  if (/作者|谁画|谁做|artist|who painted/i.test(query)) return `《${work.name}》的作者是${work.artist}。`;
  if (/年份|哪年|年代|什么时候|year|date/i.test(query)) return `这件《${work.name}》的馆方编目年代为：${work.year}。`;
  if (/材质|材料|material/i.test(query)) return "这件作品的材质为布面油画（Oil on canvas）。";
  if (/深入|详细/.test(query)) return work.deep;
  if (/孩子|儿童|小朋友/.test(query)) return work.children;
  if (/讲解|介绍|简明|简短|故事|看看/.test(query)) return work[style];
  return "这个公开演示没有连接 AI，暂未预置这一问题的回答。可以试试作者、年代、材质，或点选简明、儿童、深入讲解；更多信息请查看馆方页面。";
}
