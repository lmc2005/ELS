import { useEffect, useRef } from 'react';

type StageVariant = 'speaking' | 'retell';

interface ImmersiveStageCanvasProps {
  variant: StageVariant;
  phase: string;
  energy: number;
  secondary: number;
  accentLabel?: string;
}

interface Particle {
  x: number;
  y: number;
  z: number;
  speed: number;
}

const PARTICLE_COUNT = 110;

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function colorForVariant(variant: StageVariant) {
  return variant === 'speaking'
    ? { glow: '83, 179, 166', accent: '95, 140, 255', warning: '217, 177, 92' }
    : { glow: '95, 140, 255', accent: '217, 177, 92', warning: '223, 104, 121' };
}

export default function ImmersiveStageCanvas({
  variant,
  phase,
  energy,
  secondary,
}: ImmersiveStageCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const context = canvas.getContext('2d');
    if (!context) return;

    let frameId = 0;
    let width = 0;
    let height = 0;
    let dpr = window.devicePixelRatio || 1;
    const colors = colorForVariant(variant);
    const particles: Particle[] = Array.from({ length: PARTICLE_COUNT }, () => ({
      x: Math.random() * 2 - 1,
      y: Math.random() * 2 - 1,
      z: Math.random(),
      speed: 0.18 + Math.random() * 0.52,
    }));

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const drawPerspectiveGrid = (time: number) => {
      const horizon = height * 0.34;
      const gridDepth = clamp(secondary / 100, 0.18, 1);
      context.lineWidth = 1;

      for (let i = -8; i <= 8; i += 1) {
        const x = width / 2 + (i * width * 0.08);
        context.strokeStyle = `rgba(${colors.accent}, ${0.08 + Math.abs(i) * 0.012})`;
        context.beginPath();
        context.moveTo(width / 2, horizon);
        context.lineTo(x, height + 30);
        context.stroke();
      }

      for (let row = 0; row < 13; row += 1) {
        const depth = ((row + (time * 0.00018 * (variant === 'retell' ? 2.4 : 1.5))) % 13) / 12;
        const y = horizon + Math.pow(depth, 1.65) * height * 0.82;
        const span = width * (0.06 + depth * 0.55 * gridDepth);
        context.strokeStyle = `rgba(${colors.glow}, ${0.08 + depth * 0.18})`;
        context.beginPath();
        context.moveTo(width / 2 - span, y);
        context.lineTo(width / 2 + span, y);
        context.stroke();
      }
    };

    const drawParticles = (time: number) => {
      particles.forEach((particle, index) => {
        particle.z -= particle.speed * (variant === 'retell' ? 0.0026 : 0.0018);
        if (particle.z <= 0.02) {
          particle.z = 1;
          particle.x = Math.random() * 2 - 1;
          particle.y = Math.random() * 2 - 1;
        }

        const scale = 1 / particle.z;
        const x = width / 2 + particle.x * width * 0.42 * scale;
        const y = height / 2 + particle.y * height * 0.34 * scale;
        const size = clamp(scale * (variant === 'retell' ? 1.7 : 1.25), 0.8, 4.8);
        const alpha = clamp(0.08 + (1 - particle.z) * 0.38, 0.08, 0.5);
        context.fillStyle = `rgba(${index % 3 === 0 ? colors.warning : colors.glow}, ${alpha})`;
        context.beginPath();
        context.arc(x, y, size, 0, Math.PI * 2);
        context.fill();
      });
    };

    const drawCore = (time: number) => {
      const centerX = width * 0.66;
      const centerY = height * 0.48;
      const liveEnergy = clamp(energy / 100, 0.08, 1);
      const pulse = 1 + Math.sin(time * 0.0032) * 0.04 + liveEnergy * 0.08;
      const orbitRadius = variant === 'retell' ? 72 + secondary * 0.28 : 58 + secondary * 0.18;
      const baseRadius = variant === 'retell' ? 46 : 34;

      const glow = context.createRadialGradient(centerX, centerY, 0, centerX, centerY, orbitRadius * 1.8);
      glow.addColorStop(0, `rgba(${colors.glow}, 0.36)`);
      glow.addColorStop(0.35, `rgba(${colors.accent}, 0.14)`);
      glow.addColorStop(1, 'rgba(0, 0, 0, 0)');
      context.fillStyle = glow;
      context.beginPath();
      context.arc(centerX, centerY, orbitRadius * 1.8, 0, Math.PI * 2);
      context.fill();

      for (let ring = 0; ring < 4; ring += 1) {
        const ringRadius = orbitRadius * (0.62 + ring * 0.18) * pulse;
        context.strokeStyle = `rgba(${ring % 2 === 0 ? colors.accent : colors.warning}, ${0.22 - ring * 0.035})`;
        context.lineWidth = ring === 0 ? 2 : 1;
        context.beginPath();
        context.ellipse(
          centerX,
          centerY,
          ringRadius,
          ringRadius * (variant === 'retell' ? 0.42 : 0.56),
          time * 0.00022 * (ring % 2 === 0 ? 1 : -1),
          0,
          Math.PI * 2,
        );
        context.stroke();
      }

      context.fillStyle = `rgba(${colors.accent}, 0.95)`;
      context.beginPath();
      context.arc(centerX, centerY, baseRadius * pulse, 0, Math.PI * 2);
      context.fill();

      if (variant === 'retell') {
        for (let i = 0; i < 6; i += 1) {
          const angle = time * 0.001 + (Math.PI * 2 * i) / 6;
          const x = centerX + Math.cos(angle) * orbitRadius;
          const y = centerY + Math.sin(angle) * orbitRadius * 0.46;
          context.fillStyle = `rgba(${i % 2 === 0 ? colors.warning : colors.glow}, 0.82)`;
          context.beginPath();
          context.moveTo(x, y - 6);
          context.lineTo(x + 5, y + 5);
          context.lineTo(x - 5, y + 5);
          context.closePath();
          context.fill();
        }
      } else {
        const waveCount = 14;
        for (let i = 0; i < waveCount; i += 1) {
          const angle = (Math.PI * 2 * i) / waveCount + time * 0.0022;
          const barHeight = 14 + Math.sin(time * 0.008 + i) * 8 + secondary * 0.22;
          const x = centerX + Math.cos(angle) * (orbitRadius + 18);
          const y = centerY + Math.sin(angle) * (orbitRadius * 0.42 + 8);
          context.strokeStyle = `rgba(${colors.warning}, 0.72)`;
          context.lineWidth = 3;
          context.beginPath();
          context.moveTo(x, y);
          context.lineTo(x, y - barHeight);
          context.stroke();
        }
      }
    };

    const drawPhaseLabel = () => {
      context.fillStyle = 'rgba(237, 243, 251, 0.86)';
      context.font = '600 14px system-ui';
      context.fillText(phase.replace(/_/g, ' ').toUpperCase(), 28, 34);
      context.fillStyle = 'rgba(148, 166, 189, 0.86)';
      context.font = '500 12px system-ui';
      context.fillText(variant === 'retell' ? 'ARENA FEED' : 'CALL STAGE', 28, 54);
    };

    const render = (time: number) => {
      context.clearRect(0, 0, width, height);

      const background = context.createLinearGradient(0, 0, 0, height);
      background.addColorStop(0, variant === 'retell' ? '#090d15' : '#0b1016');
      background.addColorStop(0.45, variant === 'retell' ? '#0f1726' : '#0f1724');
      background.addColorStop(1, '#0a0f17');
      context.fillStyle = background;
      context.fillRect(0, 0, width, height);

      context.fillStyle = `rgba(${colors.accent}, 0.12)`;
      context.beginPath();
      context.ellipse(width * 0.18, height * 0.16, width * 0.26, height * 0.2, 0, 0, Math.PI * 2);
      context.fill();

      context.fillStyle = `rgba(${colors.glow}, 0.1)`;
      context.beginPath();
      context.ellipse(width * 0.84, height * 0.78, width * 0.32, height * 0.18, 0, 0, Math.PI * 2);
      context.fill();

      drawPerspectiveGrid(time);
      drawParticles(time);
      drawCore(time);
      drawPhaseLabel();

      frameId = window.requestAnimationFrame(render);
    };

    resize();
    frameId = window.requestAnimationFrame(render);

    const resizeObserver = new ResizeObserver(() => resize());
    resizeObserver.observe(canvas);

    return () => {
      window.cancelAnimationFrame(frameId);
      resizeObserver.disconnect();
    };
  }, [energy, phase, secondary, variant]);

  return <canvas ref={canvasRef} className="immersive-stage-canvas" aria-hidden="true" />;
}
