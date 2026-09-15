/** OCR identity comparison only. Never rewrite message payloads with this key. */
const MAX_TEXT_LENGTH=20_000

function normalized(text:string){
  const folded=text.normalize('NFKC').toLowerCase().replace(/\u2212/g,'-')
  return {
    key:folded.replace(/[\s\p{P}]/gu,''),
    numbers:(folded.match(/[+\-\u2212]?\s*\p{N}+(?:[.,]\p{N}+)*/gu)??[]).map(n=>n.replace(/\s/g,'')),
    negatives:folded.match(/[不没无未勿别]|\b(?:not|no|never)\b/gu)??[],
    symbols:folded.match(/[\p{S}\u200d\ufe0e\ufe0f\u20e3]/gu)??[],
  }
}

/** Banded Levenshtein: work is bounded by the permitted edit budget. */
function withinEdits(a:string[],b:string[],budget:number):boolean{
  if(Math.abs(a.length-b.length)>budget)return false
  let previous=new Int32Array(b.length+1).fill(budget+1)
  let current=new Int32Array(b.length+1).fill(budget+1)
  for(let j=0;j<=Math.min(b.length,budget);j++)previous[j]=j
  for(let i=1;i<=a.length;i++){
    const low=Math.max(1,i-budget),high=Math.min(b.length,i+budget)
    current[0]=i<=budget?i:budget+1
    if(low>1)current[low-1]=budget+1
    let best=budget+1
    for(let j=low;j<=high;j++){
      const value=Math.min(previous[j]!+1,current[j-1]!+1,previous[j-1]!+(a[i-1]===b[j-1]?0:1))
      current[j]=value;best=Math.min(best,value)
    }
    if(high<b.length)current[high+1]=budget+1
    if(best>budget)return false
    ;[previous,current]=[current,previous]
  }
  return previous[b.length]!<=budget
}

function matchesWithPolicy(first:string,current:string,minLength:number,ratio:number,maxEdits=Infinity):boolean{
  if(first.length>MAX_TEXT_LENGTH||current.length>MAX_TEXT_LENGTH)return false
  if(first===current)return first.length>0
  const a=normalized(first),b=normalized(current)
  if(a.numbers.length!==b.numbers.length||a.numbers.some((n,i)=>n!==b.numbers[i]))return false
  if(a.negatives.length!==b.negatives.length||a.negatives.some((n,i)=>n!==b.negatives[i]))return false
  if(a.symbols.length!==b.symbols.length||a.symbols.some((n,i)=>n!==b.symbols[i]))return false
  if(!a.key||!b.key)return false
  if(a.key===b.key)return true
  const left=Array.from(a.key),right=Array.from(b.key)
  if(Math.min(left.length,right.length)<minLength||Math.max(left.length,right.length)>MAX_TEXT_LENGTH)return false
  const budget=Math.min(maxEdits,Math.floor(Math.max(left.length,right.length)*ratio+1e-9))
  if(Math.abs(left.length-right.length)>budget)return false
  let start=0,leftEnd=left.length,rightEnd=right.length
  while(start<leftEnd&&start<rightEnd&&left[start]===right[start])start++
  while(leftEnd>start&&rightEnd>start&&left[leftEnd-1]===right[rightEnd-1]){leftEnd--;rightEnd--}
  if(leftEnd===start||rightEnd===start)return Math.max(leftEnd-start,rightEnd-start)<=budget
  return withinEdits(left.slice(start,leftEnd),right.slice(start,rightEnd),budget)
}

export function ocrTextMatches(first:string,current:string):boolean{
  return matchesWithPolicy(first,current,10,.1)
}

/** Names allow at most one OCR edit; callers must reject all ambiguous matches. */
export function ocrNameMatches(expected:string,observed:string):boolean{
  if(expected.length>128||observed.length>128)return false
  return matchesWithPolicy(expected,observed,5,.2,1)
}

/** Fixed UI vocabulary only, never use this short-label policy for recipients. */
export function ocrLabelMatches(expected:string,observed:string):boolean{
  if(expected.length>32||observed.length>32)return false
  return matchesWithPolicy(expected,observed,2,.5,1)
}
