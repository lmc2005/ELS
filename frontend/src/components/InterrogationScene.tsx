import { Canvas, useFrame } from '@react-three/fiber';
import { Bloom, DepthOfField, EffectComposer, Noise, Vignette } from '@react-three/postprocessing';
import { motion } from 'framer-motion';
import { useMemo, useRef } from 'react';
import * as THREE from 'three';

interface InterrogationSceneProps {
  recording: boolean;
  thinking: boolean;
  defenseMeter: number;
  collapsed: boolean;
  pressureDelta: number;
}

function LampRig({
  recording,
  thinking,
  pressureDelta,
}: Pick<InterrogationSceneProps, 'recording' | 'thinking' | 'pressureDelta'>) {
  const rig = useRef<THREE.Group | null>(null);
  useFrame(({ clock }) => {
    if (!rig.current) return;
    const swayBase = thinking ? 0.22 : 0.1;
    const urgency = recording ? 0.15 : Math.max(0, pressureDelta) * 0.003;
    rig.current.rotation.z = Math.sin(clock.elapsedTime * 0.8) * (swayBase + urgency);
  });

  return (
    <group ref={rig} position={[0, 2.55, 0]}>
      <mesh position={[0, -0.72, 0]}>
        <cylinderGeometry args={[0.03, 0.03, 1.5, 20]} />
        <meshStandardMaterial color="#363c46" metalness={0.82} roughness={0.4} />
      </mesh>
      <mesh position={[0, -1.55, 0]}>
        <cylinderGeometry args={[0.42, 0.58, 0.28, 24]} />
        <meshStandardMaterial color="#5a4332" metalness={0.42} roughness={0.82} />
      </mesh>
      <mesh position={[0, -2.02, 0]} rotation={[Math.PI, 0, 0]}>
        <coneGeometry args={[1.52, 2.6, 40, 1, true]} />
        <meshBasicMaterial
          color={recording ? '#ffe6b6' : '#ffd49a'}
          transparent
          opacity={recording ? 0.15 : 0.1}
          side={THREE.DoubleSide}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </mesh>
      <spotLight
        position={[0, -1.3, 0]}
        angle={0.62}
        penumbra={0.9}
        intensity={recording ? 56 : thinking ? 46 : 38}
        distance={10}
        color={recording ? '#fff1d0' : '#ffe0b2'}
        castShadow
      />
    </group>
  );
}

function Suspect({
  defenseMeter,
  collapsed,
  pressureDelta,
}: Pick<InterrogationSceneProps, 'defenseMeter' | 'collapsed' | 'pressureDelta'>) {
  const group = useRef<THREE.Group | null>(null);
  useFrame(({ clock }) => {
    if (!group.current) return;
    const retreat = (100 - defenseMeter) / 100;
    group.current.position.z = 0.9 + retreat * 0.58;
    group.current.position.y = 0.05 + Math.sin(clock.elapsedTime * 1.9) * 0.04;
    group.current.rotation.y = Math.sin(clock.elapsedTime * 0.42) * 0.16;
    group.current.scale.setScalar(collapsed ? 0.92 : 1 + Math.min(0.06, Math.max(0, pressureDelta) * 0.002));
  });

  return (
    <group ref={group} position={[0, -0.1, 0.95]}>
      <mesh castShadow position={[0, 0.72, 0.04]}>
        <sphereGeometry args={[0.34, 32, 32]} />
        <meshStandardMaterial
          color={collapsed ? '#6b2b38' : '#171b24'}
          emissive={collapsed ? '#351019' : '#0f1219'}
          emissiveIntensity={collapsed ? 1.35 : 0.7}
          roughness={0.82}
        />
      </mesh>
      <mesh castShadow position={[0, -0.28, 0]}>
        <capsuleGeometry args={[0.38, 1.08, 10, 20]} />
        <meshStandardMaterial
          color={collapsed ? '#3f1f28' : '#11161f'}
          emissive={collapsed ? '#290f16' : '#090b12'}
          emissiveIntensity={0.72}
          roughness={0.88}
        />
      </mesh>
      <mesh position={[-0.09, 0.72, 0.28]}>
        <sphereGeometry args={[0.028, 18, 18]} />
        <meshBasicMaterial color={collapsed ? '#ff5b63' : '#9ad6ff'} />
      </mesh>
      <mesh position={[0.09, 0.72, 0.28]}>
        <sphereGeometry args={[0.028, 18, 18]} />
        <meshBasicMaterial color={collapsed ? '#ff5b63' : '#9ad6ff'} />
      </mesh>
      <mesh position={[0, -0.68, -0.08]} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.86, 0.03, 12, 64]} />
        <meshBasicMaterial
          color={collapsed ? '#ff6169' : '#6da9ff'}
          transparent
          opacity={collapsed ? 0.34 : 0.18}
        />
      </mesh>
    </group>
  );
}

function RoomSet({
  recording,
  thinking,
  collapsed,
  defenseMeter,
}: Pick<InterrogationSceneProps, 'recording' | 'thinking' | 'collapsed' | 'defenseMeter'>) {
  const warningColor = useMemo(() => new THREE.Color(collapsed ? '#8a2d39' : '#10141d'), [collapsed]);
  const alertIntensity = Math.max(0.18, (100 - defenseMeter) / 100);

  return (
    <>
      <color attach="background" args={['#05070d']} />
      <fog attach="fog" args={['#05070d', 4.2, 10]} />
      <ambientLight intensity={0.4} color="#96a7cb" />
      <directionalLight position={[2.4, 2.8, 2.1]} intensity={0.78} color="#b1c7ee" />
      <pointLight position={[-2.3, 0.2, 1.5]} intensity={2 + alertIntensity * 6} color="#7c1e2d" />
      <pointLight position={[2.2, -0.2, 1.3]} intensity={2.4 + alertIntensity * 3} color="#4a7ca8" />

      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow position={[0, -1.08, 0]}>
        <planeGeometry args={[12, 12]} />
        <meshStandardMaterial color="#0d1118" roughness={1} />
      </mesh>
      <mesh position={[0, 1.28, -2.35]} receiveShadow>
        <boxGeometry args={[7.2, 4.6, 0.22]} />
        <meshStandardMaterial color="#121925" roughness={0.92} metalness={0.12} />
      </mesh>
      <mesh position={[0, -0.46, 0.1]} castShadow receiveShadow>
        <boxGeometry args={[3.4, 0.14, 1.5]} />
        <meshStandardMaterial color="#38404b" metalness={0.8} roughness={0.42} />
      </mesh>
      <mesh position={[-1.06, -0.28, -0.12]} castShadow>
        <boxGeometry args={[0.92, 0.22, 0.56]} />
        <meshStandardMaterial color="#515964" metalness={0.36} roughness={0.74} />
      </mesh>
      <mesh position={[1.02, -0.22, 0.08]} rotation={[-0.32, 0.08, 0.18]} castShadow>
        <boxGeometry args={[0.98, 0.05, 0.7]} />
        <meshStandardMaterial color="#7d6d59" roughness={0.95} />
      </mesh>
      <TableProps recording={recording} thinking={thinking} defenseMeter={defenseMeter} />
      <mesh position={[0.02, -0.4, -0.24]} receiveShadow>
        <torusGeometry args={[1.72, 0.06, 18, 72]} />
        <meshBasicMaterial color={warningColor} transparent opacity={0.34} />
      </mesh>
    </>
  );
}

function TableProps({
  recording,
  thinking,
  defenseMeter,
}: Pick<InterrogationSceneProps, 'recording' | 'thinking' | 'defenseMeter'>) {
  const recorder = useRef<THREE.Group | null>(null);
  const detector = useRef<THREE.Group | null>(null);

  useFrame(({ clock }) => {
    if (recorder.current) {
      const reelSpeed = recording ? 4.8 : thinking ? 2.4 : 0.15;
      recorder.current.children.forEach((child, index) => {
        if (index < 2) child.rotation.z += reelSpeed * 0.012;
      });
    }
    if (detector.current) {
      detector.current.position.y = -0.16 + Math.sin(clock.elapsedTime * 2.4) * 0.008;
    }
  });

  const meterGlow = 0.26 + (100 - defenseMeter) / 160;

  return (
    <>
      <group ref={recorder} position={[-1.06, -0.16, -0.08]}>
        <mesh castShadow>
          <boxGeometry args={[1.04, 0.2, 0.58]} />
          <meshStandardMaterial color="#545c67" metalness={0.38} roughness={0.78} />
        </mesh>
        <mesh position={[-0.24, 0.14, 0.02]} castShadow>
          <cylinderGeometry args={[0.11, 0.11, 0.05, 28]} />
          <meshStandardMaterial color="#c8ceda" metalness={0.42} roughness={0.34} />
        </mesh>
        <mesh position={[0.24, 0.14, 0.02]} castShadow>
          <cylinderGeometry args={[0.11, 0.11, 0.05, 28]} />
          <meshStandardMaterial color="#c8ceda" metalness={0.42} roughness={0.34} />
        </mesh>
        <mesh position={[0, 0.14, 0.02]} castShadow>
          <boxGeometry args={[0.2, 0.04, 0.09]} />
          <meshStandardMaterial
            color={recording ? '#ff7f6c' : thinking ? '#ffd387' : '#8ee9d7'}
            emissive={recording ? '#ff6a5c' : thinking ? '#b78b47' : '#2d6f7a'}
            emissiveIntensity={recording ? 1.8 : thinking ? 1.1 : 0.8}
          />
        </mesh>
      </group>

      <group ref={detector} position={[1.06, -0.16, -0.04]} rotation={[-0.32, 0.08, 0.18]}>
        <mesh receiveShadow>
          <boxGeometry args={[0.92, 0.05, 0.68]} />
          <meshStandardMaterial color="#7d6d59" roughness={0.95} />
        </mesh>
        <mesh position={[0, 0.04, 0.05]}>
          <planeGeometry args={[0.76, 0.18]} />
          <meshBasicMaterial
            color="#7df8b7"
            transparent
            opacity={meterGlow}
            blending={THREE.AdditiveBlending}
          />
        </mesh>
      </group>
    </>
  );
}

export default function InterrogationScene(props: InterrogationSceneProps) {
  return (
    <motion.div
      className="interrogation-scene"
      animate={{ opacity: 1, scale: 1 }}
      initial={{ opacity: 0, scale: 0.985 }}
      transition={{ duration: 0.45, ease: 'easeOut' }}
    >
      <Canvas camera={{ position: [0, 0.45, 4.35], fov: 34 }} shadows dpr={[1, 1.6]}>
        <RoomSet
          recording={props.recording}
          thinking={props.thinking}
          collapsed={props.collapsed}
          defenseMeter={props.defenseMeter}
        />
        <LampRig recording={props.recording} thinking={props.thinking} pressureDelta={props.pressureDelta} />
        <Suspect
          defenseMeter={props.defenseMeter}
          collapsed={props.collapsed}
          pressureDelta={props.pressureDelta}
        />
        <EffectComposer>
          <Bloom intensity={props.recording ? 0.65 : 0.44} luminanceThreshold={0.16} radius={0.82} />
          <DepthOfField focusDistance={0.017} focalLength={0.025} bokehScale={1.45} height={480} />
          <Noise opacity={props.thinking ? 0.08 : 0.045} />
          <Vignette eskil={false} offset={0.14} darkness={0.78} />
        </EffectComposer>
      </Canvas>
    </motion.div>
  );
}
