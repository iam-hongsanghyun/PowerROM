declare module "lucide-react" {
  import { ComponentType, SVGProps } from "react";
  type IconProps = SVGProps<SVGSVGElement> & { size?: number | string };
  type Icon = ComponentType<IconProps>;
  export const X: Icon;
  export const RefreshCw: Icon;
  export const ZoomIn: Icon;
  export const ChevronDown: Icon;
  export const ChevronUp: Icon;
  export const ChevronLeft: Icon;
  export const ChevronRight: Icon;
  export const Download: Icon;
  export const Upload: Icon;
  export const RotateCcw: Icon;
  export const Save: Icon;
}
