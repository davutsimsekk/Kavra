import { useEffect, useRef } from 'react'
import * as THREE from 'three'

export default function ThreeBackdrop() {
  const mountRef = useRef(null)

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return undefined
    if (!window.WebGLRenderingContext) {
      mount.classList.add('css-only')
      return undefined
    }
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(46, 1, 0.1, 100)
    camera.position.set(0, 0, 7)
    let renderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true, powerPreference: 'low-power' })
    } catch (_error) {
      mount.classList.add('css-only')
      return undefined
    }
    renderer.setPixelRatio(1)
    renderer.setClearColor(0x000000, 0)
    mount.appendChild(renderer.domElement)

    const knot = new THREE.Mesh(
      new THREE.TorusKnotGeometry(1.65, 0.36, 90, 12),
      new THREE.MeshBasicMaterial({ color: 0x72e6b4, wireframe: true, transparent: true, opacity: 0.13 }),
    )
    knot.position.set(2.7, -0.2, -0.4)
    scene.add(knot)

    const pointsGeometry = new THREE.BufferGeometry()
    const points = new Float32Array(120 * 3)
    for (let i = 0; i < points.length; i += 3) {
      points[i] = (Math.random() - 0.5) * 13
      points[i + 1] = (Math.random() - 0.5) * 7
      points[i + 2] = (Math.random() - 0.5) * 5
    }
    pointsGeometry.setAttribute('position', new THREE.BufferAttribute(points, 3))
    const starField = new THREE.Points(
      pointsGeometry,
      new THREE.PointsMaterial({ color: 0xa8ffe0, size: 0.025, transparent: true, opacity: 0.35 }),
    )
    scene.add(starField)

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let frame
    let contextLost = false
    const onContextLost = (event) => {
      event.preventDefault()
      contextLost = true
      cancelAnimationFrame(frame)
      renderer.domElement.style.display = 'none'
      mount.classList.add('css-only')
    }
    renderer.domElement.addEventListener('webglcontextlost', onContextLost, false)
    const resize = () => {
      const { clientWidth, clientHeight } = mount
      renderer.setSize(clientWidth, clientHeight)
      camera.aspect = clientWidth / Math.max(clientHeight, 1)
      camera.updateProjectionMatrix()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(mount)
    resize()
    const animate = () => {
      if (contextLost) return
      if (document.hidden) {
        frame = requestAnimationFrame(animate)
        return
      }
      knot.rotation.x += reducedMotion ? 0 : 0.0011
      knot.rotation.y += reducedMotion ? 0 : 0.0018
      starField.rotation.y -= reducedMotion ? 0 : 0.00025
      renderer.render(scene, camera)
      frame = requestAnimationFrame(animate)
    }
    animate()

    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      renderer.domElement.removeEventListener('webglcontextlost', onContextLost)
      pointsGeometry.dispose()
      knot.geometry.dispose()
      knot.material.dispose()
      starField.material.dispose()
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [])

  return <div className="three-backdrop" ref={mountRef} aria-hidden="true" />
}
