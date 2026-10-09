import {render,screen,fireEvent,waitFor} from '@testing-library/react'
import {describe,it,expect,vi} from 'vitest'
import {PolicyEditor} from './PolicyEditor'
import {AdvisoryPanel} from './AdvisoryPanel'
import {MaterializationForm} from './MaterializationForm'
import {BatchImportForm} from './BatchImportForm'
import {FeatureWorkspace} from './FeatureWorkspace'
import type {FeatureApi,Policy} from './feature-api'
const policy:Policy={feature_set:'risk',expected_partitions:['us'],max_age_seconds:300,grace_seconds:0,version:3,updated_at:'2026-01-01T00:00:00Z',last_evaluated_at:null}
describe('operator workflows',()=>{
 it('saves an edited policy with the observed version and renders conflicts',async()=>{
  const savePolicy=vi.fn().mockRejectedValue(new Error('Policy changed; reload before retrying'))
  render(<PolicyEditor api={{savePolicy} as unknown as FeatureApi} policy={policy} onSaved={vi.fn()}/>)
  fireEvent.change(screen.getByLabelText('Maximum source age (seconds)'),{target:{value:'600'}})
  fireEvent.click(screen.getByRole('button',{name:'Save policy'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('Policy changed')
  expect(savePolicy).toHaveBeenCalledWith('risk',expect.objectContaining({max_age_seconds:600,expected_version:3}))
 })
 it('does not request AI advice until the operator opts in and displays disabled status',async()=>{
  const advisory=vi.fn().mockResolvedValue({status:'disabled',text:'Advice disabled',advisory_only:true,evaluated_at:'now'})
  render(<AdvisoryPanel api={{advisory} as unknown as FeatureApi} feature="risk"/>)
  expect(advisory).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'Explain freshness'}))
  expect(await screen.findByRole('status')).toHaveTextContent('Advice disabled');expect(advisory).toHaveBeenCalledWith('risk')
 })
 it('preserves the event ID across an exact replay and reports that no duplicate was created',async()=>{
  const ingest=vi.fn().mockResolvedValue({created:false,event_id:'job-1'});const refreshed=vi.fn().mockResolvedValue(undefined)
  render(<MaterializationForm api={{ingest} as unknown as FeatureApi} policy={policy} onSaved={refreshed}/>)
  fireEvent.change(screen.getByLabelText('Event ID'),{target:{value:'job-1'}})
  fireEvent.click(screen.getByRole('button',{name:'Record materialization'}))
  expect(await screen.findByRole('status')).toHaveTextContent('no duplicate')
  expect(ingest).toHaveBeenCalledWith('risk',expect.objectContaining({event_id:'job-1',partition:'us'}))
  await waitFor(()=>expect(refreshed).toHaveBeenCalledOnce())
 })
 it('imports batch materializations and displays batch summary',async()=>{
  const batchIngest=vi.fn().mockResolvedValue({total:2,created:2,replayed:0,event_ids:['b1','b2']})
  const refreshed=vi.fn().mockResolvedValue(undefined)
  render(<BatchImportForm api={{batchIngest} as unknown as FeatureApi} policy={policy} onSaved={refreshed}/>)
  fireEvent.click(screen.getByRole('button',{name:'Load sample batch'}))
  fireEvent.click(screen.getByRole('button',{name:'Import materializations'}))
  expect(await screen.findByRole('status')).toHaveTextContent('2 created')
  expect(batchIngest).toHaveBeenCalledWith('risk',expect.any(Array))
  await waitFor(()=>expect(refreshed).toHaveBeenCalledOnce())
 })
 it('filters partitions by search query and exports freshness snapshot',async()=>{
  const healthData={
   feature_set:'risk',evaluated_at:'2026-01-01T12:00:00Z',
   partitions:[
    {feature_set:'risk',partition:'us-east',status:'fresh' as const,evaluated_at:'2026-01-01T12:00:00Z',event_id:'e1',source_watermark:'2026-01-01T11:59:00Z',completed_at:'2026-01-01T12:00:00Z',source_age_seconds:60,materialization_delay_seconds:60,overdue_seconds:0,due_at:null,row_count:50},
    {feature_set:'risk',partition:'eu-west',status:'stale' as const,evaluated_at:'2026-01-01T12:00:00Z',event_id:'e2',source_watermark:'2026-01-01T11:00:00Z',completed_at:'2026-01-01T12:00:00Z',source_age_seconds:3600,materialization_delay_seconds:60,overdue_seconds:3300,due_at:null,row_count:30},
   ],
   unexpected_partitions:[],healthy:false,coverage:1,stale_count:1,missing_count:0,
  }
  const mockExport=vi.fn().mockResolvedValue({feature_set:'risk',exported_at:'2026-01-01T12:00:00Z',policy,health:healthData,incidents:[]})
  const mockApi={
   policies:vi.fn().mockResolvedValue([policy]),
   health:vi.fn().mockResolvedValue(healthData),
   incidents:vi.fn().mockResolvedValue([]),
   timeline:vi.fn().mockResolvedValue({events:[],next_cursor:0}),
   audit:vi.fn().mockResolvedValue({events:[],next_cursor:0}),
   export:mockExport,
  } as unknown as FeatureApi
  render(<FeatureWorkspace api={mockApi} principal={{subject:'tester',roles:['operator']}}/>)
  expect(await screen.findByText('us-east')).toBeInTheDocument()
  expect(screen.getByText('eu-west')).toBeInTheDocument()
  fireEvent.change(screen.getByPlaceholderText('Filter partition names…'),{target:{value:'eu'}})
  expect(screen.queryByText('us-east')).not.toBeInTheDocument()
  expect(screen.getByText('eu-west')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button',{name:'Export report'}))
  expect(await screen.findByRole('status')).toHaveTextContent('Freshness snapshot exported.')
  expect(mockExport).toHaveBeenCalledWith('risk')
 })
})
