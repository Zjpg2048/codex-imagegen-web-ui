import {AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig} from "remotion";

export type ImageMotionVideoProps = {
  imageFileName: string;
  motionPrompt: string;
  durationSeconds: number;
  aspectRatio: "16:9" | "9:16" | "1:1";
};

const resolveDimensions = (aspectRatio: ImageMotionVideoProps["aspectRatio"]) => {
  if (aspectRatio === "9:16") {
    return {width: 1080, height: 1920};
  }
  if (aspectRatio === "1:1") {
    return {width: 1080, height: 1080};
  }
  return {width: 1920, height: 1080};
};

const buildMotionProfile = (motionPrompt: string) => {
  const normalized = motionPrompt.toLowerCase();
  if (normalized.includes("zoom out") || normalized.includes("pull back")) {
    return {startScale: 1.12, endScale: 1, driftX: -24, driftY: 10};
  }
  if (normalized.includes("pan left")) {
    return {startScale: 1.02, endScale: 1.08, driftX: -64, driftY: 0};
  }
  if (normalized.includes("pan right")) {
    return {startScale: 1.02, endScale: 1.08, driftX: 64, driftY: 0};
  }
  return {startScale: 1, endScale: 1.1, driftX: 18, driftY: -12};
};

export const calculateImageMotionMetadata = async ({props}: {props: ImageMotionVideoProps}) => {
  const dimensions = resolveDimensions(props.aspectRatio);
  return {
    durationInFrames: Math.max(1, Math.round(props.durationSeconds * 30)),
    fps: 30,
    width: dimensions.width,
    height: dimensions.height,
  };
};

export const ImageMotionVideo = ({imageFileName, motionPrompt}: ImageMotionVideoProps) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const motion = buildMotionProfile(motionPrompt);
  const progress = interpolate(frame, [0, durationInFrames - 1], [0, 1], {
    easing: Easing.bezier(0.22, 1, 0.36, 1),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const scale = interpolate(progress, [0, 1], [motion.startScale, motion.endScale]);
  const translateX = interpolate(progress, [0, 1], [0, motion.driftX]);
  const translateY = interpolate(progress, [0, 1], [0, motion.driftY]);
  const overlayOpacity = interpolate(frame, [0, durationInFrames * 0.35, durationInFrames - 1], [0.24, 0.1, 0.2], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const src = staticFile(imageFileName);

  return (
    <AbsoluteFill style={{backgroundColor: "#020617", overflow: "hidden"}}>
      <Img
        src={src}
        style={{
          position: "absolute",
          inset: -80,
          width: "calc(100% + 160px)",
          height: "calc(100% + 160px)",
          objectFit: "cover",
          filter: "blur(42px) brightness(0.6)",
          transform: `scale(${scale + 0.08}) translate(${translateX / 2}px, ${translateY / 2}px)`,
        }}
      />
      <AbsoluteFill
        style={{
          background: `linear-gradient(135deg, rgba(15, 23, 42, ${overlayOpacity}) 0%, rgba(59, 130, 246, ${overlayOpacity * 0.75}) 100%)`,
        }}
      />
      <AbsoluteFill style={{padding: 48, justifyContent: "center", alignItems: "center"}}>
        <Img
          src={src}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "contain",
            borderRadius: 28,
            boxShadow: "0 30px 80px rgba(0, 0, 0, 0.35)",
            transform: `scale(${scale}) translate(${translateX}px, ${translateY}px)`,
          }}
        />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
