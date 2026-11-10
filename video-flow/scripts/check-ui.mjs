import assert from 'node:assert/strict';
import {mkdtempSync, rmSync, mkdirSync} from 'node:fs';
import path from 'node:path';
import {tmpdir} from 'node:os';
import {chromium} from '@playwright/test';
import {createApp} from '../server/app.mjs';
import {configuration, appDir} from '../server/config.mjs';

const root=mkdtempSync(path.join(tmpdir(),'flow-ui-')),config=configuration({DATA_DIR:root},appDir),runtime=createApp(config);
const server=runtime.app.listen(0,'127.0.0.1');await new Promise((resolve)=>server.once('listening',resolve));
let browser;
try{
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',(error)=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);await page.getByRole('button',{name:'＋ Новый проект'}).click();
  const modal=page.getByRole('dialog');await modal.getByLabel('Название',{exact:true}).fill('Проверка интерфейса');await modal.getByLabel('Сценарий',{exact:true}).fill('Первая сцена.\n\nВторая сцена.\n\nТретья сцена.');await modal.getByRole('button',{name:'Создать проект',exact:true}).click();
  await page.getByRole('heading',{name:'Проверка интерфейса',exact:true}).waitFor();assert.ok(page.url().includes('/draft'));
  await page.getByRole('navigation',{name:'Этапы'}).getByRole('button',{name:/Раскадровка/}).click();
  const prompt=page.getByLabel('Промпт изображения',{exact:true}).first();await prompt.fill('Тёмный кинематографичный лес');
  // Navigate before debounce: pending editor text must be flushed and survive reload.
  await page.getByRole('navigation',{name:'Этапы'}).getByRole('button',{name:/Изображения/}).click();await page.reload();
  await page.getByLabel('Промпт изображения',{exact:true}).first().waitFor();assert.equal(await page.getByLabel('Промпт изображения',{exact:true}).first().inputValue(),'Тёмный кинематографичный лес');
  assert.equal(await page.getByRole('navigation',{name:'Этапы'}).getByRole('button',{name:/Анимация/}).count(),0);
  await page.getByRole('button',{name:'Подключения и ключи'}).click();await page.getByRole('dialog').waitFor();assert.equal(await page.locator('input[type=password]').count(),4);await page.keyboard.press('Escape');
  mkdirSync(path.join(appDir,'test-results'),{recursive:true});await page.screenshot({path:path.join(appDir,'test-results/interface.png'),fullPage:true});
  assert.deepEqual(errors,[]);assert.equal(runtime.store.list().length,1);process.stdout.write('Browser smoke passed: create, navigate, autosave on navigation, reload, settings, no JavaScript errors.\n');
}finally{await browser?.close();await new Promise((resolve)=>server.close(resolve));await runtime.close();rmSync(root,{recursive:true,force:true});}
