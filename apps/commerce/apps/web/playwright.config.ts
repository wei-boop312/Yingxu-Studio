import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./e2e',timeout:90000,workers:1,use:{baseURL:'http://127.0.0.1:5173',channel:process.env.PLAYWRIGHT_CHANNEL||'chrome',headless:true,viewport:{width:1440,height:1000},trace:'off'},reporter:[['list']],outputDir:'../../work/browser-test-results'});
