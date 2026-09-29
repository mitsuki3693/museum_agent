# 手机局域网作品点击修复

日期：2026-09-29。

## 触发与原因

iPhone通过电脑Wi-Fi地址的HTTP页面打开馆语，点击Water Lilies后出现`crypto.randomUUID is not a function`。作品已经选中，但`ask()`构造请求体时抛错，聊天请求未发出。

原页面直接依赖`crypto.randomUUID()`。该API要求安全上下文，电脑localhost的成功不能证明手机LAN HTTP可用。参考：[MDN randomUUID](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/randomUUID)、[MDN getRandomValues](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/getRandomValues)。

## 修改

新增`web/src/lib/request-id.ts`，安全上下文优先使用原生UUID；缺少该方法时以`getRandomValues`生成符合v4格式的UUID，保留后端UUID校验和重复请求隔离。极旧浏览器连随机字节接口都没有时，显示可理解的中文错误，不使用时间戳代替UUID。

## 验证

`web/tests/museum-request-id.test.cjs`执行页面实际提交函数，模拟仅有getRandomValues的浏览器，验证选择作品后请求确实发出、作品ID正确、UUID格式正确且两次请求不同。修改前复现同一报错；修改后LAN HTTP和原生UUID两项检查均通过。Next.js生产构建通过，测试已加入现有GitHub工作流；尚未声称远端CI运行。

两个现有前端入口已重启加载新构建；后端未重启。局域网浏览器点击睡莲已经能到达聊天接口。首轮真实模型返回服务暂不可用，与修复前的前端异常不同，不能把HTTP 200当作模型回答成功。用户iPhone刷新后的验收仍待确认。

第二轮在同一局域网浏览器重新选择睡莲，成功显示两条讲解、引用来源及“已核对引用依据”，未出现UUID异常。桌面浏览器截图保存在本地 `.runtime/mobile-http-fixed.png`。这验证了局域网浏览器端到端调用；不冒充iPhone真机复测，也不掩盖首轮模型服务失败。
