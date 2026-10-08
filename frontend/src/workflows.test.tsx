import {render,screen,fireEvent,waitFor} from '@testing-library/react'
import {describe,it,expect,vi} from 'vitest'
import {PolicyEditor} from './PolicyEditor'
import {AdvisoryPanel} from './AdvisoryPanel'
import {MaterializationForm} from './MaterializationForm'
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
})
