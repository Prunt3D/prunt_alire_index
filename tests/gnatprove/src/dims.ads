--  GNATprove must accept what Prunt's compiler accepts. The FSF GNAT 16.1 front end gives a range-constrained
--  subtype of a dimensioned type no dimension and rejects Limits and Parameters below.

package Dims
  with SPARK_Mode
is

   type Dimensioned_Float is new Long_Float
   with
     Dimension_System =>
       ((Unit_Name => Millimeter, Unit_Symbol => "mm", Dim_Symbol => "Length"),
        (Unit_Name => Second, Unit_Symbol => "s", Dim_Symbol => "Time"));

   subtype Length is Dimensioned_Float with Dimension => (Symbol => "mm", Millimeter => 1, others => 0);
   subtype Time is Dimensioned_Float with Dimension => (Symbol => "s", Second => 1, others => 0);
   subtype Velocity is Dimensioned_Float
   with Dimension => (Symbol => "mm/s", Millimeter => 1, Second => -1);

   pragma Warnings (Off, "assumed to be");
   mm : constant Length := 1.0;
   s  : constant Time := 1.0;
   pragma Warnings (On, "assumed to be");

   type Limits is array (1 .. 4) of Length range 0.0 * mm .. Length'Last;

   type Parameters is record
      Deviation_Maxes : Limits := [others => 0.1 * mm];
      Resolution      : Length range 0.0 * mm .. Length'Last := 0.001 * mm;
   end record;

   function Travel (Speed : Velocity; Duration : Time) return Length
   with
     Pre  => Speed in 0.0 * mm / s .. 1.0E6 * mm / s and then Duration in 0.0 * s .. 1.0E6 * s,
     Post => Travel'Result >= 0.0 * mm;

end Dims;
