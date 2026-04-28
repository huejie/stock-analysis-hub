import { onBeforeUnmount, type Ref } from 'vue'
import * as echarts from 'echarts'

export function useChart(containerRef: Ref<HTMLElement | null>) {
  let chart: echarts.ECharts | null = null
  let resizeTimer: ReturnType<typeof setTimeout> | null = null

  const setOption = (option: echarts.EChartsOption) => {
    if (!containerRef.value) return
    if (!chart || chart.isDisposed()) {
      chart = echarts.init(containerRef.value, undefined, { renderer: 'canvas' })
    }
    chart.setOption(option)
  }

  const resize = () => {
    if (resizeTimer) clearTimeout(resizeTimer)
    resizeTimer = setTimeout(() => chart?.resize(), 150)
  }

  const dispose = () => {
    chart?.dispose()
    chart = null
  }

  onBeforeUnmount(() => {
    window.removeEventListener('resize', resize)
    dispose()
  })

  return { setOption, dispose }
}
