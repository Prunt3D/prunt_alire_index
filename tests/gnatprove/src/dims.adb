package body Dims
  with SPARK_Mode
is

   function Travel (Speed : Velocity; Duration : Time) return Length is
   begin
      return Speed * Duration;
   end Travel;

end Dims;
