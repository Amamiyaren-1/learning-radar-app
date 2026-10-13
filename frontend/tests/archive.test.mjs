import test from 'node:test';
import assert from 'node:assert/strict';
import {configureRecords,records,archiveColumns,columnFiles,fileLocation,rebaseRows} from '../src/reference/data.ts';
import {fileAtCell,selectionCell,wrap,nearestOccurrence,LOOP_COLUMNS,LOOP_ROWS} from '../src/reference/archive-loop.ts';
const items=count=>Array.from({length:count},(_,i)=>({id:`repo:fixture/project-${i}`,title:`fixture ${i}`,url:`/history?project=fixture%2Fproject-${i}`,description:'fixture',tags:i%3===0?['a','b']:i%3===1?['b']:[]}));
test('0/1/7/8/9/40/100 identities independent of fixed physical pool',()=>{
  for(const count of [0,1,7,8,9,40,100]){
    configureRecords(items(count));
    assert.equal(records.length,count);assert.equal(LOOP_COLUMNS*LOOP_ROWS,576);
    for(let i=0;i<count;i++){const position=fileLocation(i);assert.equal(fileAtCell(position),i);assert.ok(position.slot<576);}
    if(count===0){assert.equal(fileAtCell({lane:0,row:12}),undefined);assert.equal(wrap(2,0),0);}
  }
});
test('uneven and empty groups preserve every column phase through both coordinate rebases',()=>{
  configureRecords(items(40),['all','a','b','empty']);
  assert.deepEqual(columnFiles(3),[]);
  const original=[];for(let lane=-9;lane<10;lane++)for(let row=2090;row<2130;row++)original.push({lane,row,index:fileAtCell({lane,row})});
  rebaseRows(2100);
  for(const {lane,row,index} of original)assert.equal(fileAtCell({lane:lane-8,row:row-2100}),index);
  for(let i=0;i<40;i++)assert.equal(fileAtCell(fileLocation(i)),i);
  assert.equal(nearestOccurrence(12,100,0),12);
});
test('continuous selection follows current tag group, shared identity and wrap',()=>{
  configureRecords(items(9));
  const lane=archiveColumns.indexOf('a');const files=columnFiles(lane);let cell={lane,row:12};
  for(let turn=0;turn<3000;turn++){const current=fileAtCell(cell);const next=files[wrap(files.indexOf(current)+1,files.length)];cell=selectionCell(next,cell,{axis:'row',direction:1});assert.equal(fileAtCell(cell),next);if(cell.row>2048){const shift=cell.row;rebaseRows(shift);cell.row-=shift;assert.equal(fileAtCell(cell),next);}}
});
test('stable identity can be reselected after filtering, removed selection falls back explicitly',()=>{
  const data=items(100),identity=data[40].id;configureRecords(data);assert.equal(records[40].id,identity);
  configureRecords(data.slice(35,45));const found=records.findIndex(r=>r.id===identity);assert.equal(found,5);assert.equal(fileAtCell(fileLocation(found)),found);
  configureRecords(data.slice(0,9));assert.equal(records.findIndex(r=>r.id===identity),-1);
});
