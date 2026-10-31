import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const out=new URL('../design/v2/screenshots/',import.meta.url);await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000}});const page=await context.newPage();const errors=[],latencies=[],steps=[];
page.on('pageerror',e=>errors.push(e.message));await page.exposeFunction('recordDraft',n=>{if(n>=0)latencies.push(n);});
await page.goto('http://127.0.0.1:8766');await page.getByRole('heading',{name:'研究对话',exact:true}).waitFor();
await page.evaluate(()=>{let last='';new MutationObserver(()=>{const text=document.querySelector('.stream-draft p')?.textContent||'';if(text&&text!==last){last=text;const n=Number(text.split('·').at(-1)?.trim());if(n)window.recordDraft(Date.now()-n);}}).observe(document.body,{childList:true,subtree:true,characterData:true});});
await page.getByRole('textbox',{name:'输入研究问题'}).fill('合成问题，验证流式输出');await page.getByRole('button',{name:'发送问题',exact:true}).click();
await page.locator('.stream-draft').waitFor();await page.screenshot({path:fileURLToPath(new URL('stream-draft-1440.png',out)),fullPage:true});await page.getByText('这是合成浏览器测试答案。',{exact:true}).waitFor({timeout:15000});steps.push('stream draft then final');
await page.getByRole('button',{name:'收藏回答',exact:true}).click();await page.getByRole('button',{name:'编辑并创建分支 ↗'}).click();await page.getByRole('combobox',{name:'对话分支',exact:true}).selectOption({label:'主分支'});await page.getByText('这是合成浏览器测试答案。',{exact:true}).waitFor();steps.push('edit fork and return main');
await page.getByRole('button',{name:'重新生成',exact:true}).click();await page.getByRole('button',{name:'停止回答'}).click();await page.getByRole('combobox',{name:'对话分支',exact:true}).selectOption({label:'主分支'});steps.push('regenerate fork and stop');
await page.getByRole('button',{name:'收藏',exact:true}).click();await page.getByRole('button',{name:'回到上下文 ↗'}).click();await page.getByText('这是合成浏览器测试答案。',{exact:true}).waitFor();steps.push('bookmark returns to branch');
await page.getByRole('button',{name:'知识库',exact:true}).click();await page.getByRole('textbox',{name:'筛选作者'}).fill('MISSING');await page.waitForTimeout(500);if(await page.locator('.document-row:not(.table-head)').count())throw Error('empty filter leaked rows');await page.getByRole('button',{name:'清除筛选'}).click();await page.locator('.document-row:not(.table-head)').first().waitFor();steps.push('metadata filter empty then reset');
await page.getByText('文献信息与标签',{exact:true}).first().click();await page.getByRole('textbox',{name:'标题',exact:true}).fill('人工修订合成标题');await page.getByRole('button',{name:'保存文献信息'}).click();await page.getByText('文献信息与标签',{exact:true}).first().click();steps.push('metadata save');
for(const width of [1440,1024,390]){
 await page.setViewportSize({width,height:1000});await page.waitForTimeout(350);await page.screenshot({path:fileURLToPath(new URL(`library-${width}.png`,out)),fullPage:true});
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);if(overflow)errors.push(`library horizontal overflow ${width}`);
 if(width===390)await page.getByRole('button',{name:'打开导航'}).click();await page.getByRole('button',{name:'个性化设置',exact:true}).click();await page.getByRole('button',{name:'模型配置',exact:true}).click();await page.getByRole('tab',{name:'问答 Agent'}).waitFor();await page.getByLabel('API 地址',{exact:true}).waitFor();if(width===1440){await page.getByLabel('模型名称',{exact:true}).fill('synthetic-model-v2');await page.getByRole('button',{name:'保存模型配置',exact:true}).click();await page.getByText('模型配置已保存；历史任务保持原版本',{exact:true}).waitFor();steps.push('model revision save without probe');}await page.waitForTimeout(350);await page.screenshot({path:fileURLToPath(new URL(`models-${width}.png`,out)),fullPage:true});
 if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))errors.push(`models horizontal overflow ${width}`);
 if(width===390)await page.getByRole('button',{name:'打开导航'}).click();await page.getByRole('button',{name:'知识库',exact:true}).click();await page.waitForTimeout(350);
}
latencies.sort((a,b)=>a-b);const p95=latencies[Math.min(latencies.length-1,Math.ceil(latencies.length*.95)-1)];
const report={fixture:'synthetic local-only 8766',provider_requests:0,steps,draft_samples:latencies.length,draft_to_browser_p95_ms:p95,errors};await fs.writeFile(new URL('../design/v2/browser-validation.json',import.meta.url),JSON.stringify(report,null,2));console.log(JSON.stringify(report));await browser.close();if(errors.length||!latencies.length||p95>500)process.exitCode=1;
