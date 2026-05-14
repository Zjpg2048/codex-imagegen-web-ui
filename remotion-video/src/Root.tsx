import {Composition} from "remotion";
import {ImageMotionVideo, type ImageMotionVideoProps, calculateImageMotionMetadata} from "./ImageMotionVideo";

export const RemotionRoot = () => {
  return (
    <Composition<ImageMotionVideoProps>
      id="ImageMotionVideo"
      component={ImageMotionVideo}
      durationInFrames={120}
      fps={30}
      width={1280}
      height={720}
      defaultProps={{
        imageFileName: "input/placeholder.png",
        motionPrompt: "Create a subtle cinematic camera move.",
        durationSeconds: 4,
        aspectRatio: "16:9",
      }}
      calculateMetadata={calculateImageMotionMetadata}
    />
  );
};
