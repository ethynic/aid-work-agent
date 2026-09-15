/** Local legacy-driver bridge. Payloads stay on stdin; stdout contains a boolean only. */
import {readFileSync} from 'node:fs'
import {ocrNameMatches,ocrTextMatches} from './ocrTextMatch.js'

try{
  const input=readFileSync(0)
  if(input.length>200_000)throw new Error('input_limit')
  const value=JSON.parse(input.toString('utf8')) as {mode?:unknown;first?:unknown;second?:unknown}
  if(!['name','text'].includes(String(value.mode))||typeof value.first!=='string'||typeof value.second!=='string')throw new Error('invalid_input')
  const matched=(value.mode==='name'?ocrNameMatches:ocrTextMatches)(value.first,value.second)
  process.stdout.write(JSON.stringify({matched}))
}catch{
  process.stderr.write('OCR_MATCH_INVALID_INPUT')
  process.exitCode=1
}
