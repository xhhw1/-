import { lazy, Suspense, type ComponentType, type LazyExoticComponent } from "react";

const DotField = lazy(() => import("./DotField")) as LazyExoticComponent<ComponentType<any>>;

type BackgroundProps = {
  className?: string;
};

export function DotFieldBackground({ className = "" }: BackgroundProps) {
  return (
    <div className={`reactbits-bg dot-field-bg ${className}`.trim()} aria-hidden="true">
      <Suspense fallback={null}>
        <DotField
          dotRadius={2}
          dotSpacing={15}
          bulgeStrength={67}
          glowRadius={160}
          sparkle={false}
          waveAmplitude={0}
          cursorRadius={500}
          cursorForce={0.1}
          bulgeOnly
          gradientFrom="#06f506"
          gradientTo="#7b01ee"
          glowColor="#e0f0e1"
        />
      </Suspense>
    </div>
  );
}
