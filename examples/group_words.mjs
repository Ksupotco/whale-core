// Local Chinese word segmentation using the ICU dictionary shipped with Node.
import { readFileSync } from 'node:fs';
const segmenter = new Intl.Segmenter('zh-CN', { granularity: 'word' });
const stop = new Set('我们 你们 他们 这个 那个 什么 怎么 就是 然后 因为 所以 但是 可以 可能 应该 感觉 自己 知道 现在 没有 这样 那样 真的 还是 一个 一点 一下 时候 已经 有点 好像 确实 觉得 哈哈 哈哈哈 图片 表情 回复 转发 消息 动画 表情包 不是 不会 不过 这种 那种 如果 不知道 其实 也是 一直 甚至 为什么 看到 这么 那么 这里 还有 那些 这些 别人'.split(' '));
const words = JSON.parse(readFileSync(0, 'utf8')).map(text => {
  const clean = text.replace(/https?:\/\/\S+|\[[^\]\n]{1,300}\]|[a-f0-9]{16,}/gi, ' ');
  return [...new Set(Array.from(segmenter.segment(clean), t => t.isWordLike ? t.segment.toLowerCase() : '')
    .filter(w => w.length >= 2 && w.length <= 20 && !stop.has(w) && !/^\d+$/.test(w)))];
});
process.stdout.write(JSON.stringify(words));
