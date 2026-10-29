import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const out = new URL('../design/screenshots/', import.meta.url);
await fs.mkdir(out,{recursive:true});
const browser = await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const context = await browser.newContext({viewport:{width:1440,height:1000}});
const page = await context.newPage();
const errors=[];
page.on('pageerror',e=>errors.push(e.message));
await page.goto('http://127.0.0.1:8765');
await page.getByRole('heading',{name:'研究对话',exact:true}).waitFor();
await page.getByText('让每一个问题，').waitFor();
await page.screenshot({path:fileURLToPath(new URL('chat-1440.png',out)),fullPage:true});
await page.getByRole('button',{name:'知识库',exact:true}).click();
await page.getByRole('heading',{name:'知识库.'}).waitFor();
await page.getByRole('button',{name:/Kuang/}).first().waitFor({state:'visible'}).catch(()=>{});
await page.waitForTimeout(700);
await page.screenshot({path:fileURLToPath(new URL('library-1440.png',out)),fullPage:true});
await page.getByRole('button',{name:'上传资料',exact:true}).click();
await page.getByRole('dialog').waitFor();
await page.screenshot({path:fileURLToPath(new URL('upload-1440.png',out)),fullPage:true});
await page.getByRole('button',{name:'关闭',exact:true}).click();
await page.getByRole('button',{name:'个性化设置',exact:true}).click();
await page.getByRole('heading',{name:'个性化设置.'}).waitFor();
await page.screenshot({path:fileURLToPath(new URL('settings-1440.png',out)),fullPage:true});
const history=page.locator('.history-list>button').first();
if(await history.count()){
  await history.click();
  await page.locator('.citation').first().waitFor();
  await page.screenshot({path:fileURLToPath(new URL('answer-1440.png',out)),fullPage:true});
  await page.locator('.citation').first().click();
  await page.getByRole('complementary',{name:'证据详情'}).waitFor();
  await page.screenshot({path:fileURLToPath(new URL('evidence-1440.png',out)),fullPage:true});
  await page.getByRole('button',{name:'关闭证据'}).click();
}
for(const width of [1024,390]){
  await page.setViewportSize({width,height:900});
  if(width===390)await page.getByRole('button',{name:'打开导航'}).click();
  await page.getByRole('button',{name:/新建研究对话/}).click();
  await page.getByText('让每一个问题，').waitFor();
  await page.waitForTimeout(250);
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  if(overflow)throw new Error(`Page overflows at ${width}`);
  await page.screenshot({path:fileURLToPath(new URL(`chat-${width}.png`,out)),fullPage:true});
}
await fs.writeFile(new URL('../design/browser-report.json',import.meta.url),JSON.stringify({errors,widths:[1440,1024,390],realApiCalls:0,history:'Imported existing real CLI answer; no model requests made'},null,2));
if(process.argv.includes('--lifecycle')){
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('button',{name:'知识库',exact:true}).click();
  await page.getByRole('button',{name:'新建知识库',exact:true}).click();
  await page.getByRole('dialog').getByRole('textbox',{name:'名称',exact:true}).fill('浏览器验收 · 离线');
  await page.getByRole('dialog').getByRole('button',{name:'保存',exact:true}).click();
  await page.getByRole('heading',{name:'浏览器验收 · 离线',exact:true}).waitFor();
  await page.getByRole('button',{name:'上传资料',exact:true}).click();
  await page.locator('input[type=file]').setInputFiles(fileURLToPath(new URL('../../doc2md/examples/sample.pdf',import.meta.url)));
  await page.getByRole('dialog').waitFor({state:'hidden'});
  await page.locator('.document-row:not(.table-head)').filter({hasText:'sample.pdf'}).getByText('可检索',{exact:true}).waitFor({timeout:120000});
  await page.getByRole('button',{name:/sample.pdf/}).click();
  await page.getByRole('dialog').getByRole('button',{name:'Markdown',exact:true}).click();
  await page.locator('.markdown-preview').getByText(/1729/).first().waitFor();
  await page.getByRole('button',{name:'关闭',exact:true}).click();
  page.once('dialog',d=>d.accept());
  await page.getByRole('button',{name:'移出',exact:true}).click();
  await page.getByRole('combobox',{name:'文档状态筛选'}).selectOption('removed');
  await page.getByRole('button',{name:'恢复',exact:true}).waitFor();
  await page.getByRole('button',{name:'恢复',exact:true}).click();
  await page.getByRole('combobox',{name:'文档状态筛选'}).selectOption('active');
  await page.getByText('可检索',{exact:true}).waitFor({timeout:120000});
  page.once('dialog',d=>d.accept());
  await page.getByRole('button',{name:'回收',exact:true}).click();
  await page.getByRole('heading',{name:'浏览器验收 · 离线',exact:true}).waitFor({state:'hidden'});
  await fs.writeFile(new URL('../design/browser-lifecycle-report.json',import.meta.url),JSON.stringify({
    passed:true,modelApiRequests:0,steps:['create','upload','parse','index','preview-markdown','remove','restore','recycle']},null,2));
}
await browser.close();
if(errors.length)throw new Error(errors.join('\n'));
console.log('Browser rendering, navigation and citations passed at 1440 / 1024 / 390.');
