import test from 'node:test'
import assert from 'node:assert/strict'
import { uniqueContactRow } from '../src/platform/nameSession.js'

test('唯一联系人要求分区闭合，通用更多/查看更多/展开均拒绝',()=>{
 const row=(text:string,y:number)=>({text,score:0.99,x0:20,x1:110,y0:y,y1:y+15})
 const visible=[row('联系人',10),row('target',40),row('群聊',120)]
 assert.equal(uniqueContactRow(visible,300,200,'target').text,'target')
 const frequent=[row('最常使用',10),row('target',40),row('群聊',120)]
 assert.equal(uniqueContactRow(frequent,300,200,'target').text,'target')
 assert.throws(()=>uniqueContactRow([...frequent,row('查看更多',80)],300,200,'target'))
 assert.throws(()=>uniqueContactRow(frequent.slice(0,2),300,200,'target'))
 assert.throws(()=>uniqueContactRow([...frequent,row('target',65)],300,200,'target'))
 assert.throws(()=>uniqueContactRow([...frequent,row('联系人',90)],300,200,'target'))
 for(const hidden of ['查看更多','更多','展开','…'])assert.throws(()=>uniqueContactRow([...visible,row(hidden,80)],300,200,'target'))
 assert.throws(()=>uniqueContactRow(visible.slice(0,2),300,200,'target'))
 assert.throws(()=>uniqueContactRow([...visible,row('target',65)],300,200,'target'))
})
