import test from 'node:test'
import assert from 'node:assert/strict'
import {spawnSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'
const cli=fileURLToPath(new URL('../src/platform/ocrMatchCli.js',import.meta.url))
const run=(input:string)=>spawnSync(process.execPath,[cli],{input,encoding:'utf8',timeout:5000,shell:false})

test('JSON stdin preserves Unicode quotes and shell-like text without outputting payload',()=>{
 for(const text of ['中文“引号”与全角ＡＢＣ','特殊字符 $HOME $(echo hello) ; & | \n emoji😀']){
  const result=run(JSON.stringify({mode:'text',first:text,second:text}))
  assert.equal(result.status,0);assert.equal(result.stderr,'');assert.equal(result.stdout,'{"matched":true}')
 }
 const result=run(JSON.stringify({mode:'text',first:'“你好，世界！”',second:'你好 世界'}))
 assert.equal(result.status,0);assert.equal(result.stdout,'{"matched":true}')
})

test('CLI applies separate name and text policy rather than trusting the mode prompt',()=>{
 for(const [mode,matched] of [['name',true],['text',false]] as const){
  const result=run(JSON.stringify({mode,first:'abcde',second:'abcdf'}))
  assert.equal(result.status,0);assert.deepEqual(JSON.parse(result.stdout),{matched})
 }
})

test('invalid input exits nonzero with fixed diagnostics and no private input echo',()=>{
 for(const input of ['private secret invalid JSON',JSON.stringify({mode:'wrong',first:'private secret',second:'private secret'}),JSON.stringify({mode:'text',first:3,second:'private secret'}),'x'.repeat(200001)]){
  const result=run(input)
  assert.equal(result.status,1);assert.equal(result.stdout,'');assert.equal(result.stderr,'OCR_MATCH_INVALID_INPUT')
 }
})
